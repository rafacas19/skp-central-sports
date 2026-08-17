"""Dashboard routes (server-rendered, Spanish UI).

Every page depends on `auth.require_dashboard`; only /dashboard/login and the
static files are reachable without a session. Later phases extend NAV and add
their routes here — nav items appear as their pages land, so each phase ships
without dead links.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import settings
from ..models import FEET, Prospect
from ..positions import ROLES
from ..storage import Storage
from . import auth, forms, photos, queries, summaries

router = APIRouter(prefix="/dashboard", include_in_schema=False)

_BASE = Path(__file__).parent
static_files = StaticFiles(directory=_BASE / "static")
templates = Jinja2Templates(directory=_BASE / "templates")

# The scout works in Colombia; timestamps are stored UTC.
_TZ = queries.TZ

NAV = [
    ("Resumen", "/dashboard"),
    ("Partidos", "/dashboard/partidos"),
    ("Jugadores", "/dashboard/jugadores"),
    ("Decisiones", "/dashboard/decisiones"),
]


def _fecha(value: datetime | None) -> str:
    """dd/mm/yyyy in the scout's timezone; empty for None."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_TZ).strftime("%d/%m/%Y")


def _hora(value: datetime | None) -> str:
    """HH:MM in the scout's timezone; empty for None."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_TZ).strftime("%H:%M")


def _valoracion(value: float | None) -> str:
    """A 1–5 rating without a spurious trailing .0 (4.0 → «4», 4.5 → «4.5»)."""
    if value is None:
        return "—"
    return f"{value:g}"


def _minuto(value: int | None) -> str:
    return f"{value}'" if value is not None else "—"


def _valor(value: int | None) -> str:
    """A USD estimate, abbreviated the way market values are quoted in Spanish
    («$1,2 M», «$250 mil»). Decimal comma, as in es-CO."""
    if not value:
        return "—"
    if value >= 1_000_000:
        millions = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"${millions.replace('.', ',')} M"
    if value >= 1_000:
        return f"${value // 1000} mil"
    return f"${value}"


templates.env.filters["fecha"] = _fecha
templates.env.filters["hora"] = _hora
templates.env.filters["valoracion"] = _valoracion
templates.env.filters["minuto"] = _minuto
templates.env.filters["valor"] = _valor


def _render(
    request: Request, template: str, context: dict, status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template,
        {
            "nav": NAV,
            "path": request.url.path,
            "csrf": auth.make_csrf_token(),
            "csrf_field": auth.CSRF_FIELD,
            **context,
        },
        status_code=status_code,
    )


# ── Login / logout (no session required) ─────────────────────────────────
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not settings.dashboard_password:
        return _render(
            request, "login.html",
            {"error": "El panel no está configurado (falta DASHBOARD_PASSWORD)."},
        )
    return _render(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(request: Request, password: str = Form(default="")):
    if not settings.dashboard_password:
        return _render(
            request, "login.html",
            {"error": "El panel no está configurado (falta DASHBOARD_PASSWORD)."},
        )
    ip = auth.client_ip(request)
    if not auth.register_attempt(ip):
        return _render(
            request, "login.html",
            {"error": "Demasiados intentos. Espera unos minutos y vuelve a probar."},
        )
    if not auth.check_password(password):
        return _render(request, "login.html", {"error": "Contraseña incorrecta."})

    auth.clear_attempts(ip)
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.make_session_token(),
        max_age=auth.SESSION_TTL_S,
        httponly=True,
        secure=auth.cookie_secure(),
        samesite="lax",
        path="/dashboard",
    )
    return response


@router.post("/logout")
async def logout(csrf: str = Form(default="")):
    auth.require_csrf(csrf)
    response = RedirectResponse("/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(auth.COOKIE_NAME, path="/dashboard")
    return response


# ── Pages ────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse, dependencies=[Depends(auth.require_dashboard)])
async def overview(request: Request):
    data = await queries.overview()
    return _render(request, "overview.html", data)


def _parse_date(raw: str | None) -> date | None:
    """A YYYY-MM-DD query param; anything malformed is treated as unset."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@router.get(
    "/partidos", response_class=HTMLResponse, dependencies=[Depends(auth.require_dashboard)]
)
async def matches_page(
    request: Request,
    competicion: str | None = None,
    estado: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
):
    data = await queries.list_matches(
        competition=competicion or None,
        state=estado or None,
        date_from=_parse_date(desde),
        date_to=_parse_date(hasta),
    )
    filters = {
        "competicion": competicion or "",
        "estado": estado or "",
        "desde": desde or "",
        "hasta": hasta or "",
    }
    filters["any"] = any(filters.values())
    return _render(request, "matches.html", {**data, "filters": filters})


@router.get(
    "/partidos/{session_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def match_page(request: Request, session_id: int):
    data = await queries.match_detail(session_id)
    if data is None:
        return _render(
            request, "not_found.html",
            {"message": "Ese partido no existe.", "back": "/dashboard/partidos"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _render(request, "match_detail.html", data)


@router.get(
    "/jugadores", response_class=HTMLResponse, dependencies=[Depends(auth.require_dashboard)]
)
async def players_page(
    request: Request,
    q: str | None = None,
    equipo: str | None = None,
    decision: str | None = None,
    valoracion_min: str | None = None,
):
    try:
        rating_min = float(valoracion_min) if valoracion_min else None
    except ValueError:
        rating_min = None
    data = await queries.list_players(
        q=q or None,
        team=equipo or None,
        decision=decision or None,
        rating_min=rating_min,
    )
    filters = {
        "q": q or "",
        "equipo": equipo or "",
        "decision": decision or "",
        "valoracion_min": valoracion_min if rating_min is not None else "",
    }
    filters["any"] = any(filters.values())
    return _render(request, "players.html", {**data, "filters": filters})


@router.get(
    "/decisiones", response_class=HTMLResponse, dependencies=[Depends(auth.require_dashboard)]
)
async def decisions_page(request: Request):
    groups = await queries.decision_board()
    return _render(request, "decisions.html", {"groups": groups})


@router.get(
    "/jugadores/{prospect_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def player_page(request: Request, prospect_id: int, background_tasks: BackgroundTasks):
    data = await queries.player_detail(prospect_id)
    if data is None:
        return _render(
            request, "not_found.html",
            {"message": "Ese jugador no existe.", "back": "/dashboard/jugadores"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    summary = await summaries.get_or_refresh(prospect_id, background_tasks)
    return _render(request, "player_detail.html", {**data, "summary": summary})


@router.get(
    "/foto/{prospect_id}", dependencies=[Depends(auth.require_dashboard)]
)
async def player_photo(prospect_id: int):
    """Proxy the player's Telegram photo.

    Telegram needs the bot token to serve the file, so the browser can never
    fetch it directly. A missing or unreachable photo is a 404, which the card
    already handles by showing initials."""
    p = await Prospect.get_or_none(id=prospect_id)
    if p is None or not p.photo_file_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    payload = await photos.fetch(p.photo_file_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    content, content_type = payload
    return Response(
        content,
        media_type=content_type,
        # The file_id is immutable, so the browser can hold on to it.
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ── Editing a player ─────────────────────────────────────────────────────
def _form_values(p: Prospect) -> dict[str, str]:
    """Current prospect values keyed by form field name, as strings."""

    def s(value: object | None) -> str:
        return "" if value is None else str(value)

    return {
        "nombre": p.name or "",
        "equipo": p.team or "",
        "posicion": p.position or "",
        "dorsal": s(p.shirt_number),
        "anio_nacimiento": s(p.birth_year),
        "edad": s(p.age),
        "estatura": s(p.height_cm),
        "peso": s(p.weight_kg),
        "pie": p.preferred_foot or "",
        "nacionalidad": p.nationality or "",
        "procedencia": p.origin_club or "",
        "valor": s(p.market_value_usd),
        "contrato_hasta": s(p.contract_year),
        "agente": p.agent_name or "",
        "telefono_agente": p.agent_phone or "",
        "valoracion": f"{p.latest_rating:g}" if p.latest_rating is not None else "",
        "decision": p.decision_status or "",
        "notas": p.notes or "",
    }


def _edit_context(p: Prospect, values: dict[str, str]) -> dict:
    """Everything the edit form needs: current values, option lists, and the
    player's own off-list position/rating kept as selectable options so opening
    and saving the form never silently rewrites what the bot captured."""
    positions = [r.role for r in ROLES]
    current_position = (values.get("posicion") or "").strip()
    if current_position and current_position not in positions:
        positions = [current_position, *positions]
    ratings = list(forms.RATING_CHOICES)
    current_rating = (values.get("valoracion") or "").strip()
    if current_rating and current_rating not in ratings:
        ratings = sorted([*ratings, current_rating], key=float)
    return {
        "player": {"id": p.id, "name": queries.display_name(p)},
        "values": values,
        "positions": positions,
        "feet": FEET,
        "ratings": ratings,
        "decisions": forms.DECISION_LABELS,
    }


@router.get(
    "/jugadores/{prospect_id}/editar",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def player_edit_page(request: Request, prospect_id: int):
    p = await queries.get_prospect(prospect_id)
    if p is None:
        return _render(
            request, "not_found.html",
            {"message": "Ese jugador no existe.", "back": "/dashboard/jugadores"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    context = _edit_context(p, _form_values(p))
    return _render(request, "player_edit.html", {**context, "errors": {}, "collision": None})


@router.post(
    "/jugadores/{prospect_id}/editar", dependencies=[Depends(auth.require_dashboard)]
)
async def player_edit_submit(request: Request, prospect_id: int):
    p = await queries.get_prospect(prospect_id)
    if p is None:
        return _render(
            request, "not_found.html",
            {"message": "Ese jugador no existe.", "back": "/dashboard/jugadores"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    submitted = {k: str(v) for k, v in (await request.form()).items()}
    auth.require_csrf(submitted.get(auth.CSRF_FIELD))

    updates, errors = forms.parse_player_form(submitted, current_position=p.position)
    context = _edit_context(p, {**_form_values(p), **submitted})
    if errors:
        return _render(
            request, "player_edit.html",
            {**context, "errors": errors, "collision": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    forms.apply_identity(updates)
    # Renaming into a player who already exists would split one person across two
    # records. Offer the merge instead of saving, keeping what was typed on screen.
    if "normalized_name" in updates:
        other = await queries.identity_collision(
            p,
            updates["normalized_name"],
            updates.get("normalized_team", p.normalized_team or ""),
        )
        if other is not None:
            return _render(
                request, "player_edit.html",
                {
                    **context,
                    "errors": {},
                    "collision": {"id": other.id, "name": queries.display_name(other)},
                },
                status_code=status.HTTP_409_CONFLICT,
            )

    await Storage().update_prospect(prospect_id, **updates)
    return RedirectResponse(
        f"/dashboard/jugadores/{prospect_id}", status_code=status.HTTP_303_SEE_OTHER
    )


# ── Merging two profiles of the same player ──────────────────────────────
@router.get(
    "/jugadores/{prospect_id}/fusionar",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def player_merge_page(request: Request, prospect_id: int, con: int | None = None):
    data = await queries.merge_candidates(prospect_id)
    if data is None:
        return _render(
            request, "not_found.html",
            {"message": "Ese jugador no existe.", "back": "/dashboard/jugadores"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    selected = next((c for c in data["candidates"] if c["id"] == con), None)
    return _render(request, "player_merge.html", {**data, "selected": selected})


@router.post(
    "/jugadores/{prospect_id}/fusionar", dependencies=[Depends(auth.require_dashboard)]
)
async def player_merge_submit(
    request: Request,
    prospect_id: int,
    con: int = Form(...),
    csrf: str = Form(default=""),
):
    auth.require_csrf(csrf)
    keep = await Prospect.get_or_none(id=prospect_id)
    drop = await Prospect.get_or_none(id=con)
    if keep is None or drop is None or keep.id == drop.id:
        return _render(
            request, "not_found.html",
            {"message": "No se pudo fusionar: alguno de los perfiles no existe.",
             "back": "/dashboard/jugadores"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    await Storage().merge_prospects(keep.id, drop.id)
    return RedirectResponse(
        f"/dashboard/jugadores/{keep.id}", status_code=status.HTTP_303_SEE_OTHER
    )
