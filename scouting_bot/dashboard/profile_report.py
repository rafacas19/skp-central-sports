"""The player profile report — the client's "Detección de talento" sheet.

One page per player and profile, laid out like the client's own example
(`feedback/Perfiles_Scout__EJ.pdf`): photo, name, the role, a short summary,
every criterion with one score column per evaluated match (oldest first), the
build/height checks, and per-section observations headed by the section
average ("1. Técnica — 3,5/5"). Averages are taken over every score shown, in
the client's format (one decimal, half-up, comma).

The written part is drafted by the AI provider and editable by the scout; it is
cached per (player, profile) in `ProfileReport`. When the evaluations change, an
unedited text is redrafted in the background, while an edited one is kept and
the page offers to regenerate it — an edit is never overwritten silently.

The PDF is the same sheet rendered server-side with WeasyPrint.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..ai import get_provider
from ..evaluations import match_when
from ..models import Evaluation, Observation, ProfileReport, Prospect, ProspectPhoto
from ..positions import canonical_position
from ..profiles import Profile, average, format_average, get_profile
from ..taxonomy import normalize_name
from . import auth, photos, queries
from .router import _render, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", include_in_schema=False)

# The PDF renderer resolves `static/profile_report.css` against this directory.
_BASE = Path(__file__).parent


def _tri(value: bool | None) -> str:
    return "" if value is None else ("Sí" if value else "No")


def watermark(evaluations: list[Evaluation]) -> str:
    """Fingerprint of the sheets a text was written from: any new, removed or
    re-saved sheet changes it."""
    return "|".join(
        f"{e.id}:{e.updated_at.isoformat()}" for e in sorted(evaluations, key=lambda e: e.id)
    )


async def _evaluations(prospect_id: int) -> list[Evaluation]:
    rows = await (
        Evaluation.filter(prospect_id=prospect_id, profile__not_isnull=True)
        .prefetch_related("session")
    )
    return sorted(rows, key=lambda e: (match_when(e.session), e.id))


def _opponent(prospect: Prospect, e: Evaluation) -> str:
    s = e.session
    if prospect.team and normalize_name(prospect.team) == normalize_name(s.away_team):
        return s.home_team
    if prospect.team and normalize_name(prospect.team) == normalize_name(s.home_team):
        return s.away_team
    return f"{s.home_team} vs {s.away_team}"


def report_sheet(prospect: Prospect, profile: Profile, evaluations: list[Evaluation]) -> dict:
    """The numbers of the report: matches as columns, sections as rows."""
    matches = [
        {
            "label": f"PARTIDO {i}",
            "date": match_when(e.session),
            "opponent": _opponent(prospect, e),
            "session_id": e.session_id,
        }
        for i, e in enumerate(evaluations, start=1)
    ]
    sections = []
    every_score = []
    for s in profile.sections:
        criteria = []
        shown = []
        for c in s.criteria:
            scores = [e.scores.get(c.code) for e in evaluations]
            shown += [v for v in scores if v is not None]
            criteria.append(
                {"code": c.code, "name": c.name, "description": c.description, "scores": scores}
            )
        every_score += shown
        sections.append(
            {
                "number": s.number,
                "title": s.title,
                "header": s.header,
                "average": format_average(average(shown)),
                "criteria": criteria,
            }
        )
    role = canonical_position(prospect.position)
    return {
        # The client calls the goalkeeper "Arquero" (their sheet's title).
        "role": (role.role if role and role.role != "Portero" else profile.name),
        "matches": matches,
        "sections": sections,
        "overall": format_average(average(every_score)),
        "build": {"expected": profile.build, "values": [_tri(e.build_ok) for e in evaluations]},
        "height": {"expected": profile.height, "values": [_tri(e.height_ok) for e in evaluations]},
    }


async def _notes(prospect_id: int, evaluations: list[Evaluation]) -> list[str]:
    """What the scout wrote about the player in the evaluated matches."""
    sessions = [e.session_id for e in evaluations]
    obs = await Observation.filter(
        prospect_id=prospect_id, session_id__in=sessions
    ).order_by("created_at", "id")
    notes = [o.raw_quote for o in obs if o.raw_quote and not o.raw_quote.startswith("Evaluación por perfil")]
    return notes[:40]


def ai_payload(prospect: Prospect, profile: Profile, sheet: dict, notes: list[str]) -> dict:
    return {
        "player": prospect.name or queries.display_name(prospect),
        "role": sheet["role"],
        "profile": profile.name,
        "matches": [
            {"label": m["label"], "date": m["date"].date().isoformat(), "opponent": m["opponent"]}
            for m in sheet["matches"]
        ],
        "sections": [
            {
                "number": s["number"],
                "title": s["title"],
                "average": s["average"],
                "criteria": [
                    {"code": c["code"], "name": c["name"], "scores": c["scores"]}
                    for c in s["criteria"]
                ],
            }
            for s in sheet["sections"]
        ],
        "build": sheet["build"],
        "height": sheet["height"],
        "notes": notes,
    }


async def draft(prospect_id: int, profile_key: str) -> ProfileReport:
    """Ask the AI for the text and store it as a fresh, unedited draft."""
    prospect = await Prospect.get(id=prospect_id)
    profile = get_profile(profile_key)
    evaluations = [e for e in await _evaluations(prospect_id) if e.profile == profile_key]
    sheet = report_sheet(prospect, profile, evaluations)
    payload = ai_payload(prospect, profile, sheet, await _notes(prospect_id, evaluations))
    text = await get_provider().draft_profile_report(payload)
    report, _ = await ProfileReport.get_or_create(prospect_id=prospect_id, profile=profile_key)
    report.summary = text.get("summary") or ""
    report.sections = {
        str(s.number): (text.get("sections") or {}).get(str(s.number), "")
        for s in profile.sections
    }
    report.edited = False
    report.watermark = watermark(evaluations)
    await report.save()
    return report


async def _redraft_quietly(prospect_id: int, profile_key: str) -> None:
    try:
        await draft(prospect_id, profile_key)
    except Exception:  # noqa: BLE001 — a background failure keeps the old text
        logger.exception("Profile report redraft failed for prospect %s", prospect_id)


async def report_context(
    prospect: Prospect, requested: str | None, background: BackgroundTasks | None = None
) -> dict | None:
    """Everything the report page (and the PDF) shows, or None when the player
    has no sheet scored on a profile yet."""
    all_evaluations = await _evaluations(prospect.id)
    keys = []
    for e in reversed(all_evaluations):  # latest first: the default profile
        if e.profile not in keys:
            keys.append(e.profile)
    if not keys:
        return None
    key = requested if requested in keys else keys[0]
    profile = get_profile(key)
    evaluations = [e for e in all_evaluations if e.profile == key]
    sheet = report_sheet(prospect, profile, evaluations)

    text = await ProfileReport.get_or_none(prospect_id=prospect.id, profile=key)
    stale = text is not None and text.watermark != watermark(evaluations)
    refreshing = False
    if stale and not text.edited and background is not None:
        background.add_task(_redraft_quietly, prospect.id, key)
        refreshing = True
    await prospect.fetch_related("observations")
    return {
        "player": {
            "id": prospect.id,
            "name": queries.display_name(prospect),
            "has_photo": bool(prospect.photo_file_id)
            or await ProspectPhoto.filter(prospect_id=prospect.id).exists(),
            "video_url": prospect.video_url,
        },
        "profile": {"key": profile.key, "name": profile.name},
        "profiles": [{"key": k, "name": get_profile(k).name} for k in keys],
        "sheet": sheet,
        "text": {
            "exists": text is not None,
            "summary": text.summary if text else "",
            "sections": text.sections if text else {},
            "edited": bool(text and text.edited),
            "stale": stale,
            "refreshing": refreshing,
        },
    }


def _report_url(prospect_id: int, suffix: str = "", **params) -> str:
    url = f"/dashboard/jugadores/{prospect_id}/informe{suffix}"
    params = {k: v for k, v in params.items() if v}
    return f"{url}?{urlencode(params)}" if params else url


def _missing(request: Request):
    return _render(
        request, "not_found.html",
        {"message": "Ese jugador no existe.", "back": "/dashboard/jugadores"},
        status_code=status.HTTP_404_NOT_FOUND,
    )


@router.get(
    "/jugadores/{prospect_id}/informe",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def report_page(
    request: Request,
    prospect_id: int,
    background_tasks: BackgroundTasks,
    perfil: str | None = None,
    aviso: str | None = None,
):
    prospect = await Prospect.get_or_none(id=prospect_id)
    if prospect is None:
        return _missing(request)
    context = await report_context(prospect, perfil, background_tasks)
    if context is None:
        return _render(
            request, "profile_report_empty.html",
            {"player": {"id": prospect.id, "name": queries.display_name(prospect)}},
        )
    return _render(request, "profile_report.html", {**context, "aviso": aviso})


@router.post(
    "/jugadores/{prospect_id}/informe/generar",
    dependencies=[Depends(auth.require_dashboard)],
)
async def report_generate(
    request: Request, prospect_id: int, perfil: str = Form(...), csrf: str = Form(default="")
):
    """Draft (or redraft) the text now. Replaces an edited text too — the scout
    pressed the button on purpose."""
    auth.require_csrf(csrf)
    if await Prospect.get_or_none(id=prospect_id) is None or get_profile(perfil) is None:
        return _missing(request)
    aviso = None
    try:
        await draft(prospect_id, perfil)
    except Exception:  # noqa: BLE001 — a broken AI must never break the page
        logger.exception("Profile report draft failed for prospect %s", prospect_id)
        aviso = "ia_error"
    return RedirectResponse(
        _report_url(prospect_id, perfil=perfil, aviso=aviso), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get(
    "/jugadores/{prospect_id}/informe/texto",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def report_text_page(request: Request, prospect_id: int, perfil: str | None = None):
    prospect = await Prospect.get_or_none(id=prospect_id)
    if prospect is None:
        return _missing(request)
    context = await report_context(prospect, perfil)
    if context is None:
        return RedirectResponse(_report_url(prospect_id), status_code=status.HTTP_303_SEE_OTHER)
    return _render(request, "profile_report_text.html", context)


@router.post(
    "/jugadores/{prospect_id}/informe/texto",
    dependencies=[Depends(auth.require_dashboard)],
)
async def report_text_submit(request: Request, prospect_id: int):
    """Save the scout's text. It counts as reviewed against the current sheets,
    so the "new evaluations" notice goes until the next one arrives."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    auth.require_csrf(form.get(auth.CSRF_FIELD))
    profile = get_profile(form.get("perfil"))
    if await Prospect.get_or_none(id=prospect_id) is None or profile is None:
        return _missing(request)
    evaluations = [e for e in await _evaluations(prospect_id) if e.profile == profile.key]
    report, _ = await ProfileReport.get_or_create(prospect_id=prospect_id, profile=profile.key)
    report.summary = form.get("resumen", "").strip()
    report.sections = {
        str(s.number): form.get(f"seccion-{s.number}", "").strip() for s in profile.sections
    }
    report.edited = True
    report.watermark = watermark(evaluations)
    await report.save()
    return RedirectResponse(
        _report_url(prospect_id, perfil=profile.key), status_code=status.HTTP_303_SEE_OTHER
    )


async def _photo_data_uri(prospect: Prospect) -> str | None:
    """The photo inlined for the PDF renderer, which can't reach the proxy.
    An uploaded photo wins over the Telegram one, as everywhere else."""
    uploaded = await ProspectPhoto.get_or_none(prospect_id=prospect.id)
    if uploaded is not None:
        return f"data:{uploaded.mime};base64,{base64.b64encode(bytes(uploaded.data)).decode()}"
    if not prospect.photo_file_id:
        return None
    payload = await photos.fetch(prospect.photo_file_id)
    if payload is None:
        return None
    content, content_type = payload
    return f"data:{content_type};base64,{base64.b64encode(content).decode()}"


def print_html(context: dict, photo_src: str | None = None) -> str:
    """The report as a standalone page for the PDF renderer."""
    return templates.get_template("profile_report_print.html").render(
        {**context, "photo_src": photo_src}
    )


def render_document(html: str):
    """Lay the page out with WeasyPrint (heavy import, only when needed)."""
    from weasyprint import HTML

    return HTML(string=html, base_url=str(_BASE)).render()


def render_pdf(html: str) -> bytes:
    return render_document(html).write_pdf()


@router.get("/jugadores/{prospect_id}/informe.pdf", dependencies=[Depends(auth.require_dashboard)])
async def report_pdf(request: Request, prospect_id: int, perfil: str | None = None):
    prospect = await Prospect.get_or_none(id=prospect_id)
    if prospect is None:
        return _missing(request)
    context = await report_context(prospect, perfil)
    if context is None:
        return RedirectResponse(_report_url(prospect_id), status_code=status.HTTP_303_SEE_OTHER)
    pdf = render_pdf(print_html(context, await _photo_data_uri(prospect)))
    title = f"Perfil {context['player']['name']} - {context['profile']['name']}"
    disposition = f"attachment; filename*=UTF-8''{quote(title + '.pdf')}"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": disposition})
