"""Dashboard routes (server-rendered, Spanish UI).

Every page depends on `auth.require_dashboard`; only /dashboard/login and the
static files are reachable without a session. Later phases extend NAV and add
their routes here — nav items appear as their pages land, so each phase ships
without dead links.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
_TZ = ZoneInfo("America/Bogota")

# (label, href) — extended by later phases (Partidos, Jugadores, Decisiones).
NAV = [("Resumen", "/dashboard")]


def _fecha(value: datetime | None) -> str:
    """dd/mm/yyyy in the scout's timezone; empty for None."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_TZ).strftime("%d/%m/%Y")


def _valoracion(value: float | None) -> str:
    """A 1–5 rating without a spurious trailing .0 (4.0 → «4», 4.5 → «4.5»)."""
    if value is None:
        return "—"
    return f"{value:g}"


templates.env.filters["fecha"] = _fecha
templates.env.filters["valoracion"] = _valoracion


def _render(request: Request, template: str, context: dict) -> HTMLResponse:
    return templates.TemplateResponse(
        request, template, {"nav": NAV, "path": request.url.path, **context}
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
