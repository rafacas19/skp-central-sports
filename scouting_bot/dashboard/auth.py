"""Dashboard authentication: shared password → HMAC-signed session cookie.

Mirrors the read API's philosophy (`API_KEY`): when DASHBOARD_PASSWORD is not
configured the dashboard is disabled (503), never open. The cookie is a
stdlib-HMAC token `<expiry-ts>.<hexdigest>` — no extra dependency; the signing
key is DASHBOARD_SECRET (falling back to the password itself so a single env
var is enough locally).

Login attempts are rate-limited per client IP with a simple in-memory window.
That is deliberately process-local: the service runs as a single instance and
the goal is only to blunt password guessing, not to be a distributed limiter.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status

from ..config import settings

COOKIE_NAME = "dashboard_session"
SESSION_TTL_S = 30 * 24 * 3600  # 30 days

# Rate limit: at most MAX_ATTEMPTS failed logins per IP per WINDOW_S.
MAX_ATTEMPTS = 10
WINDOW_S = 15 * 60
_attempts: dict[str, tuple[int, float]] = {}  # ip -> (count, window_start)


def _secret() -> bytes:
    return (settings.dashboard_secret or settings.dashboard_password).encode()


def _sign(expires_ts: int) -> str:
    mac = hmac.new(_secret(), f"dashboard-v1:{expires_ts}".encode(), hashlib.sha256)
    return f"{expires_ts}.{mac.hexdigest()}"


def make_session_token(now: float | None = None) -> str:
    if now is None:
        now = time.time()
    return _sign(int(now + SESSION_TTL_S))


def verify_session_token(token: str | None, now: float | None = None) -> bool:
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
    return hmac.compare_digest(_sign(expires_ts), token)


def check_password(candidate: str) -> bool:
    return hmac.compare_digest(candidate.encode(), settings.dashboard_password.encode())


def register_attempt(ip: str) -> bool:
    """Record a login attempt for `ip`. Returns False when over the limit."""
    now = time.time()
    count, started = _attempts.get(ip, (0, now))
    if now - started > WINDOW_S:
        count, started = 0, now
    count += 1
    _attempts[ip] = (count, started)
    if len(_attempts) > 1000:  # bound memory; stale windows are re-derived anyway
        _attempts.clear()
        _attempts[ip] = (count, started)
    return count <= MAX_ATTEMPTS


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


async def require_dashboard(request: Request) -> None:
    """Route dependency: valid session cookie or redirect to the login page."""
    if not settings.dashboard_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard is not configured (set DASHBOARD_PASSWORD).",
        )
    if not verify_session_token(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/dashboard/login"},
        )
