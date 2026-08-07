"""Dashboard routes (server-rendered, Spanish UI).

Every page depends on `auth.require_dashboard`; only /dashboard/login and the
static files are reachable without a session. Later phases extend NAV and add
their routes here — nav items appear as their pages land, so each phase ships
without dead links.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import settings
from . import auth, queries

router = APIRouter(prefix="/dashboard", include_in_schema=False)

_BASE = Path(__file__).parent
static_files = StaticFiles(directory=_BASE / "static")
templates = Jinja2Templates(directory=_BASE / "templates")

# The scout works in Colombia; timestamps are stored UTC.
_TZ = queries.TZ

# (label, href) — extended by later phases (Decisiones).
NAV = [
    ("Resumen", "/dashboard"),
    ("Partidos", "/dashboard/partidos"),
    ("Jugadores", "/dashboard/jugadores"),
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


templates.env.filters["fecha"] = _fecha
templates.env.filters["hora"] = _hora
templates.env.filters["valoracion"] = _valoracion
templates.env.filters["minuto"] = _minuto


def _render(
    request: Request, template: str, context: dict, status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template,
        {"nav": NAV, "path": request.url.path, **context},
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
async def logout():
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
    "/jugadores/{prospect_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def player_page(request: Request, prospect_id: int):
    data = await queries.player_detail(prospect_id)
    if data is None:
        return _render(
            request, "not_found.html",
            {"message": "Ese jugador no existe.", "back": "/dashboard/jugadores"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _render(request, "player_detail.html", data)
