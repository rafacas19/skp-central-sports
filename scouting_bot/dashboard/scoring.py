"""Scoring players from the dashboard: the per-match score sheet.

A sheet scores one player in one match on his position profile (see
`profiles.py`); saving it goes through `evaluations.save_evaluation`, which
mirrors it onto an ordinary rated observation so every existing page and export
keeps working. Players without a profile (goalkeepers, until the client sends
that sheet) get a single 1–5 rating instead.

Everything here works as plain HTML forms. The one script (`scoring.js`) only
autosaves the sheet; without it the Guardar button does the same.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..evaluations import (
    MergeConflict,
    carry_scores,
    clean_single_rating,
    save_evaluation,
)
from ..models import Evaluation, MatchPlayer, Observation, Prospect, Session
from ..positions import ROLES
from .. import profiles
from ..profiles import (
    SCORES,
    Profile,
    average,
    default_profile,
    format_average,
    get_profile,
    is_complete,
    match_rating,
)
from ..storage import Storage
from ..taxonomy import normalize_identity
from . import auth, queries
from .pitch import is_unnamed, name_player
from .router import _render

router = APIRouter(prefix="/dashboard", include_in_schema=False)

# The profile switcher's value for "no profile, single rating".
NO_PROFILE = "ninguno"


def _missing(request: Request, message: str, back: str = "/dashboard/partidos"):
    return _render(
        request, "not_found.html", {"message": message, "back": back},
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _sheet_url(session_id: int, prospect_id: int, **params) -> str:
    url = f"/dashboard/partidos/{session_id}/jugadores/{prospect_id}/evaluar"
    params = {k: v for k, v in params.items() if v}
    return f"{url}?{urlencode(params)}" if params else url


def _choose_profile(
    evaluation: Evaluation | None, prospect: Prospect, requested: str | None
) -> Profile | None:
    """The profile the sheet shows: the one asked for in the switcher, else the
    one the sheet was saved with, else the default for the player's position."""
    if requested == NO_PROFILE:
        return None
    if requested and get_profile(requested) is not None:
        return get_profile(requested)
    if evaluation is not None:
        return get_profile(evaluation.profile)
    return default_profile(prospect.position)


def _tri(value: bool | None) -> str:
    return "" if value is None else ("si" if value else "no")


def _parse_tri(raw: str | None) -> bool | None:
    return {"si": True, "no": False}.get((raw or "").strip().lower())


def sheet_sections(profile: Profile, scores: dict[str, int]) -> list[dict]:
    """The profile's sections with each criterion's score and the section
    average, in the client's format ("3,7")."""
    sections = []
    for s in profile.sections:
        values = [scores.get(c.code) for c in s.criteria]
        sections.append(
            {
                "number": s.number,
                "title": s.title,
                "short": s.short,
                "scored": sum(v is not None for v in values),
                "total": len(values),
                "average": format_average(average(values)),
                "criteria": [
                    {
                        "code": c.code,
                        "name": c.name,
                        "description": c.description,
                        "score": scores.get(c.code),
                    }
                    for c in s.criteria
                ],
            }
        )
    return sections


async def _sheet_context(
    session: Session, prospect: Prospect, requested: str | None
) -> dict:
    evaluation = await Evaluation.get_or_none(session_id=session.id, prospect_id=prospect.id)
    stored = get_profile(evaluation.profile) if evaluation is not None else None
    profile = _choose_profile(evaluation, prospect, requested)

    scores: dict[str, int] = {}
    lost = 0
    switched = evaluation is not None and (profile.key if profile else None) != evaluation.profile
    if evaluation is not None and profile is not None:
        if switched:
            scores = carry_scores(stored, profile, evaluation.scores)
            lost = len(evaluation.scores) - len(scores)
        else:
            scores = dict(evaluation.scores)
    elif evaluation is not None and switched:
        lost = len(evaluation.scores)

    lineup = await MatchPlayer.get_or_none(session_id=session.id, prospect_id=prospect.id)
    number = lineup.shirt_number if lineup is not None and lineup.shirt_number is not None else None
    if number is None:
        number = prospect.shirt_number
    evaluated = await Evaluation.filter(session_id=session.id).count()
    rating = match_rating(scores) if profile is not None else (
        evaluation.rating if evaluation is not None and not switched else None
    )
    await prospect.fetch_related("observations")
    back = (f"/dashboard/partidos/{session.id}/campo" if lineup is not None
            else f"/dashboard/partidos/{session.id}#evaluaciones")
    actions = [
        {"href": f"/dashboard/jugadores/{prospect.id}", "label": "Ficha del jugador", "icon": "user"},
    ]
    if evaluation is not None and profile is not None:
        actions.append({"href": f"/dashboard/jugadores/{prospect.id}/informe?perfil={profile.key}",
                        "label": "Informe de perfil", "icon": "file"})
    actions += [
        {"href": f"/dashboard/partidos/{session.id}/campo", "label": "Campo", "icon": "pitch", "sep": True},
        {"href": f"/dashboard/partidos/{session.id}", "label": "Detalle del partido", "icon": "list"},
    ]
    return {
        "bar": {
            "back": back,
            "back_label": "Volver al campo" if lineup is not None else "Volver al partido",
            "title": f"{session.home_team} vs {session.away_team}",
            "meta": f"{evaluated} puntuados" if evaluated else "Sin puntuar todavía",
            "actions": actions,
        },
        "match": {
            "id": session.id,
            "home_team": session.home_team,
            "away_team": session.away_team,
            "date": session.match_date or session.created_at,
            "evaluated": evaluated,
        },
        "player": {
            "id": prospect.id,
            "name": queries.display_name(prospect),
            "team": prospect.team,
            "position": prospect.position,
            "unnamed": is_unnamed(prospect),
            "number": number,
        },
        "profile": profile,
        "profiles": profiles.PROFILES,
        "no_profile": NO_PROFILE,
        "scores_range": SCORES,
        "sections": sheet_sections(profile, scores) if profile is not None else [],
        "scored": len(scores),
        "total": len(profile.criteria) if profile is not None else 0,
        "complete": is_complete(profile, scores) if profile is not None else rating is not None,
        "rating": rating,
        "build_ok": _tri(evaluation.build_ok) if evaluation is not None and not switched else "",
        "height_ok": _tri(evaluation.height_ok) if evaluation is not None and not switched else "",
        "note": evaluation.note if evaluation is not None else "",
        "saved": evaluation is not None,
        "switched": switched,
        "lost": lost,
    }


async def _pair(session_id: int, prospect_id: int):
    session = await Session.get_or_none(id=session_id)
    prospect = await Prospect.get_or_none(id=prospect_id)
    return session, prospect


@router.get(
    "/partidos/{session_id}/jugadores/{prospect_id}/evaluar",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def sheet_page(
    request: Request,
    session_id: int,
    prospect_id: int,
    perfil: str | None = None,
    guardado: str | None = None,
    identificado: str | None = None,
):
    session, prospect = await _pair(session_id, prospect_id)
    if session is None or prospect is None:
        return _missing(request, "Ese partido o ese jugador no existe.")
    context = await _sheet_context(session, prospect, perfil)
    return _render(
        request, "evaluation.html",
        {**context, "just_saved": bool(guardado), "identified": bool(identificado),
         "collision": None, "identify_values": {}, "identify_error": None},
    )


@router.post(
    "/partidos/{session_id}/jugadores/{prospect_id}/evaluar",
    dependencies=[Depends(auth.require_dashboard)],
)
async def sheet_submit(request: Request, session_id: int, prospect_id: int):
    form = {k: str(v) for k, v in (await request.form()).items()}
    auth.require_csrf(form.get(auth.CSRF_FIELD))
    session, prospect = await _pair(session_id, prospect_id)
    if session is None or prospect is None:
        return _missing(request, "Ese partido o ese jugador no existe.")

    profile = get_profile(form.get("perfil"))
    await save_evaluation(
        session,
        prospect,
        profile=profile,
        raw_scores={k[2:]: v for k, v in form.items() if k.startswith("s-")},
        build_ok=_parse_tri(form.get("contextura")),
        height_ok=_parse_tri(form.get("estatura")),
        note=form.get("nota"),
        single_rating=clean_single_rating(form.get("valoracion")),
    )
    return RedirectResponse(
        _sheet_url(session_id, prospect_id, guardado="1"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/partidos/{session_id}/jugadores/{prospect_id}/evaluar/borrar",
    dependencies=[Depends(auth.require_dashboard)],
)
async def sheet_delete(
    request: Request, session_id: int, prospect_id: int, csrf: str = Form(default="")
):
    """Throw a sheet away, with the observation that mirrors it.

    The player's headline rating is left as it is: it may have come from
    another match, and the next rating he gets replaces it anyway."""
    auth.require_csrf(csrf)
    evaluation = await Evaluation.get_or_none(session_id=session_id, prospect_id=prospect_id)
    if evaluation is not None:
        if evaluation.observation_id is not None:
            await Observation.filter(id=evaluation.observation_id).delete()
        await evaluation.delete()
    return RedirectResponse(
        f"/dashboard/partidos/{session_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/partidos/{session_id}/evaluar", dependencies=[Depends(auth.require_dashboard)]
)
async def evaluate_another(
    request: Request,
    session_id: int,
    nombre: str = Form(default=""),
    lado: str = Form(default=""),
    dorsal: str = Form(default=""),
    posicion: str = Form(default=""),
    csrf: str = Form(default=""),
):
    """Open a sheet for a player nobody noted in this match yet.

    The player is found or created the same way the bot does it — by name and
    club — so an existing profile is reused, never duplicated. A number with no
    name becomes a temporary profile for that match, as it does in the bot."""
    auth.require_csrf(csrf)
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request, "Ese partido no existe.")
    team = session.away_team if lado == "away" else session.home_team
    name = nombre.strip()
    number = int(dorsal) if dorsal.strip().isdigit() else None
    position = posicion.strip() if posicion.strip() in {r.role for r in ROLES} else None
    if not name and number is None:
        return RedirectResponse(
            f"/dashboard/partidos/{session_id}?aviso=faltan_datos#evaluaciones",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    storage = Storage()
    if name:
        prospect = await storage.get_or_create_prospect(
            session.agent_chat_id, name, team, position=position
        )
    else:
        prospect = await storage.get_or_create_temp_prospect(
            session.agent_chat_id, session.id, team, number
        )
        if position and not prospect.position:
            prospect.position = position
    if number is not None and prospect.shirt_number is None:
        prospect.shirt_number = number
    await prospect.save()
    return RedirectResponse(
        _sheet_url(session_id, prospect.id), status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/partidos/{session_id}/jugadores/{prospect_id}/identificar",
    dependencies=[Depends(auth.require_dashboard)],
)
async def identify_player(
    request: Request,
    session_id: int,
    prospect_id: int,
    nombre: str = Form(default=""),
    dorsal: str = Form(default=""),
    unir: str = Form(default=""),
    csrf: str = Form(default=""),
):
    """Name an unnamed player and/or set his dorsal for this match — at any
    time, including after the match has ended.

    A name that already belongs to a player of the same club is the same
    person: the page asks before merging the two (the bot and the edit form ask
    the same), and only an explicit "Sí, es él" (`unir`) merges them."""
    auth.require_csrf(csrf)
    session, prospect = await _pair(session_id, prospect_id)
    if session is None or prospect is None:
        return _missing(request, "Ese partido o ese jugador no existe.")

    raw_number = dorsal.strip()
    number = int(raw_number) if raw_number.isdigit() and 0 < int(raw_number) < 100 else None
    name = nombre.strip()
    error = None
    if raw_number and number is None:
        error = "El dorsal debe ser un número de 1 a 99."
    elif len(name) > 120:
        error = "El nombre no puede pasar de 120 caracteres."

    async def again(status_code: int, **extra):
        context = await _sheet_context(session, prospect, None)
        return _render(
            request, "evaluation.html",
            {**context, "just_saved": False, "identified": False, "collision": None,
             "identify_values": {"nombre": name, "dorsal": raw_number},
             "identify_error": error, **extra},
            status_code=status_code,
        )

    if error:
        return await again(status.HTTP_400_BAD_REQUEST)

    if name and is_unnamed(prospect):
        existing = await queries.identity_taken(
            prospect.agent_chat_id, normalize_identity(name),
            prospect.normalized_team or "", exclude_id=prospect.id,
        )
        if existing is not None and unir != str(existing.id):
            return await again(
                status.HTTP_409_CONFLICT,
                collision={"id": existing.id, "name": queries.display_name(existing),
                           "team": existing.team},
            )
        try:
            prospect = await name_player(prospect, name)
        except MergeConflict as exc:
            error = str(exc)
            return await again(status.HTTP_409_CONFLICT)

    if number is not None:
        await MatchPlayer.filter(session_id=session_id, prospect_id=prospect.id).update(
            shirt_number=number
        )
        if prospect.shirt_number is None:
            prospect.shirt_number = number
            await prospect.save()
    return RedirectResponse(
        _sheet_url(session_id, prospect.id, identificado="1"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
