"""Dashboard authentication: per-user accounts → HMAC-signed session cookie.

Each person signs in with a username and password (see users.py). The cookie
is a stdlib-HMAC token `<user_id>.<session_version>.<expiry-ts>.<hexdigest>`,
so it names *who* is signed in; the user row is re-read on every request, so a
role change, a reset or a disabled account takes effect on the next click
(the bumped `session_version` invalidates every cookie issued before it). The
signing key is DASHBOARD_SECRET, falling back to DASHBOARD_PASSWORD — the old
shared password, which no longer signs anyone in. With neither set the
dashboard is disabled (503), never open.

Roles are enforced here, in the one dependency every dashboard route already
carries (`require_dashboard`), so a new route is protected by default:
  · any POST needs the `write` role (except signing out and one's own password);
  · /dashboard/usuarios needs `admin`;
  · the GET pages that only exist to change data (forms) need `write` too.
Templates hide what a role can't use, but the server is what enforces it.

Login attempts are rate-limited per client IP and per username with a simple
in-memory window. That is deliberately process-local: the service runs as a
single instance and the goal is only to blunt password guessing.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time

from fastapi import HTTPException, Request, status

from ..config import settings
from ..models import DashboardUser

COOKIE_NAME = "dashboard_session"
SESSION_TTL_S = 30 * 24 * 3600  # 30 days

# CSRF tokens are the same HMAC construction under a different purpose string
# (so a session cookie can never be replayed as a form token) and are short-lived
# — long enough to fill in a player form, short enough to be worth little.
CSRF_FIELD = "csrf"
CSRF_TTL_S = 12 * 3600

# Rate limit: at most MAX_ATTEMPTS failed logins per key per WINDOW_S, where a
# key is a client IP ("ip:…") or a username ("user:…").
MAX_ATTEMPTS = 10
WINDOW_S = 15 * 60
_attempts: dict[str, tuple[int, float]] = {}  # key -> (count, window_start)

CHANGE_PASSWORD_PATH = "/dashboard/cuenta/contrasena"


def configured() -> bool:
    return bool(settings.dashboard_secret or settings.dashboard_password)


def _secret() -> bytes:
    return (settings.dashboard_secret or settings.dashboard_password).encode()


def _sign(expires_ts: int, purpose: str = "dashboard-v1") -> str:
    mac = hmac.new(_secret(), f"{purpose}:{expires_ts}".encode(), hashlib.sha256)
    return f"{expires_ts}.{mac.hexdigest()}"


def _verify(token: str | None, purpose: str, now: float | None) -> bool:
    if now is None:
        now = time.time()
    if not token or "." not in token:
        return False
    expires_raw, _, _ = token.partition(".")
    try:
        expires_ts = int(expires_raw)
    except ValueError:
        return False
    if expires_ts < now:
        return False
    return hmac.compare_digest(_sign(expires_ts, purpose), token)


def make_session_token(user: DashboardUser, now: float | None = None) -> str:
    if now is None:
        now = time.time()
    expires = int(now + SESSION_TTL_S)
    payload = f"{user.id}.{user.session_version}.{expires}"
    mac = hmac.new(_secret(), f"dashboard-user-v2:{payload}".encode(), hashlib.sha256)
    return f"{payload}.{mac.hexdigest()}"


def read_session_token(token: str | None, now: float | None = None) -> tuple[int, int] | None:
    """(user_id, session_version) from a valid, unexpired cookie; else None.

    A cookie from the shared-password era has a different shape and purpose
    string, so it never validates here."""
    if now is None:
        now = time.time()
    parts = (token or "").split(".")
    if len(parts) != 4:
        return None
    try:
        user_id, version, expires = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if expires < now:
        return None
    payload = f"{user_id}.{version}.{expires}"
    mac = hmac.new(_secret(), f"dashboard-user-v2:{payload}".encode(), hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), parts[3]):
        return None
    return user_id, version


def make_csrf_token(now: float | None = None) -> str:
    if now is None:
        now = time.time()
    return _sign(int(now + CSRF_TTL_S), "dashboard-csrf-v1")


def verify_csrf_token(token: str | None, now: float | None = None) -> bool:
    return _verify(token, "dashboard-csrf-v1", now)


def register_attempt(key: str) -> bool:
    """Record a failed-or-pending login for `key`. False when over the limit."""
    now = time.time()
    count, started = _attempts.get(key, (0, now))
    if now - started > WINDOW_S:
        count, started = 0, now
    count += 1
    _attempts[key] = (count, started)
    if len(_attempts) > 2000:  # bound memory; stale windows are re-derived anyway
        _attempts.clear()
        _attempts[key] = (count, started)
    return count <= MAX_ATTEMPTS


def over_limit(key: str) -> bool:
    count, started = _attempts.get(key, (0, time.time()))
    return time.time() - started <= WINDOW_S and count >= MAX_ATTEMPTS


def clear_attempts(ip: str) -> None:
    _attempts.pop(ip, None)


def client_ip(request: Request) -> str:
    # Render terminates TLS and forwards the source in X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


def cookie_secure() -> bool:
    # Prod (webhook mode) is always https; local dev is plain http.
    return settings.use_webhook


def require_csrf(token: str | None) -> None:
    """Guard for every state-changing POST. The session cookie is SameSite=Lax,
    so a cross-site form post already arrives without credentials; this is the
    second lock, and it also expires stale open tabs."""
    if not verify_csrf_token(token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formulario caducado. Vuelve a cargar la página.",
        )


# GET pages that exist only to change data: forms for creating, editing,
# deleting, lineups, substitutions. A read-only user has nothing to do there.
_WRITE_PAGES = re.compile(
    r"^/dashboard/("
    r"partidos/nuevo"
    r"|partidos/\d+/(editar|borrar|sistema|alineacion|cambio)"
    r"|jugadores/nuevo"
    r"|jugadores/\d+/(editar|fusionar|informe/texto)"
    r")/?$"
)
# POSTs any signed-in user may make, whatever their role.
_ANY_ROLE_POSTS = {"/dashboard/logout", CHANGE_PASSWORD_PATH}


def _forbidden(message: str = "No tienes permiso para hacer esto. Pide acceso a un administrador.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


async def current_user(request: Request) -> DashboardUser | None:
    """The signed-in, active user behind the session cookie, or None."""
    cached = getattr(request.state, "dashboard_user", "unset")
    if cached != "unset":
        return cached
    user = None
    parsed = read_session_token(request.cookies.get(COOKIE_NAME))
    if parsed is not None:
        user_id, version = parsed
        found = await DashboardUser.get_or_none(id=user_id)
        if found is not None and found.is_active and found.session_version == version:
            user = found
    request.state.dashboard_user = user
    return user


async def require_dashboard(request: Request) -> None:
    """Route dependency: a signed-in user with the role this request needs."""
    if not configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard is not configured (set DASHBOARD_SECRET).",
        )
    user = await current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/dashboard/login"},
        )
    path = request.url.path.rstrip("/") or "/"
    # A temporary password opens one door only: choosing a real one.
    if user.must_change_password and path not in _ANY_ROLE_POSTS:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": CHANGE_PASSWORD_PATH},
        )
    if path.startswith("/dashboard/usuarios") and not user.is_admin:
        raise _forbidden("Solo un administrador puede gestionar usuarios.")
    if request.method == "POST" and path not in _ANY_ROLE_POSTS and not user.can_write:
        raise _forbidden()
    if request.method == "GET" and _WRITE_PAGES.match(path) and not user.can_write:
        raise _forbidden()
