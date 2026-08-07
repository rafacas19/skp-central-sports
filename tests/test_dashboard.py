"""Dashboard tests: auth flow (password → signed cookie) and the overview page.

Drives the real FastAPI app over httpx's ASGITransport (no lifespan — the
`storage` fixture owns Tortoise, same trick as the e2e harness). DASHBOARD
settings are injected per-test via the `dashboard_auth` fixture.
"""

from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

from scouting_bot.config import settings
from scouting_bot.dashboard import auth
from scouting_bot.models import Observation, Prospect, Session

PASSWORD = "prueba-scouting"


def _set(field: str, value: str) -> str:
    old = getattr(settings, field)
    object.__setattr__(settings, field, value)
    return old


@pytest_asyncio.fixture
async def client(storage):
    """HTTP client over the real app; `storage` gives a clean, truncated DB."""
    from scouting_bot.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def dashboard_auth():
    """Configure the dashboard password for a test; restore afterwards."""
    old_pw = _set("dashboard_password", PASSWORD)
    old_secret = _set("dashboard_secret", "")
    auth._attempts.clear()
    yield
    _set("dashboard_password", old_pw)
    _set("dashboard_secret", old_secret)
    auth._attempts.clear()


async def _login(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/dashboard/login", data={"password": password})


async def _seed() -> dict:
    """Two matches (one live), three players, mixed observations."""
    old = await Session.create(
        agent_chat_id=1, home_team="Millonarios", away_team="América",
        state="ended", competition="Liga", location="El Campín", scout_name="Wilmer",
        match_date=datetime(2026, 6, 20, 17, 0, tzinfo=timezone.utc),
        first_half_started_at=datetime(2026, 6, 20, 17, 5, tzinfo=timezone.utc),
    )
    live = await Session.create(agent_chat_id=1, home_team="Bogotá", away_team="Valle")

    ferrin = await Prospect.create(
        agent_chat_id=1, name="Jordan Ferrin", normalized_name="jordan ferrin",
        team="Millonarios", latest_rating=5,
    )
    ocampo = await Prospect.create(
        agent_chat_id=1, name="Ocampo", normalized_name="ocampo",
        team="América", latest_rating=2,
    )
    temp = await Prospect.create(
        agent_chat_id=1, name="", normalized_name="", team="Valle", is_temporary=True,
    )

    await Observation.create(
        session=old, prospect=ferrin, player_name="Jordan Ferrin",
        raw_quote="Gran juego aéreo", minute=12, rating=5,
    )
    await Observation.create(
        session=old, prospect=ocampo, player_name="Ocampo",
        raw_quote="Lento en el repliegue", minute=30,
    )
    await Observation.create(
        session=old, player_name="Rodríguez", is_substitution=True,
        raw_quote="Entra Rodríguez", minute=62,
    )
    await Observation.create(
        session=old, is_team_note=True, team="Millonarios",
        raw_quote="Presión alta tras pérdida",
    )
    await Observation.create(
        session=live, prospect=temp, player_number=7,
        raw_quote="Rápido en el 1vs1", minute=5,
    )
    return {"old": old, "live": live}


# ── Disabled state ───────────────────────────────────────────────────────
async def test_dashboard_disabled_without_password(client):
    old = _set("dashboard_password", "")
    try:
        resp = await client.get("/dashboard")
        assert resp.status_code == 503

        resp = await client.get("/dashboard/login")
        assert resp.status_code == 200
        assert "no está configurado" in resp.text
    finally:
        _set("dashboard_password", old)


# ── Auth flow ────────────────────────────────────────────────────────────
async def test_redirects_to_login_without_session(client, dashboard_auth):
    resp = await client.get("/dashboard")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


async def test_login_page_renders(client, dashboard_auth):
    resp = await client.get("/dashboard/login")
    assert resp.status_code == 200
    assert 'name="password"' in resp.text


async def test_wrong_password_rejected(client, dashboard_auth):
    resp = await _login(client, "incorrecta")
    assert resp.status_code == 200
    assert "Contraseña incorrecta" in resp.text
    assert auth.COOKIE_NAME not in client.cookies

    # And the session page still redirects.
    resp = await client.get("/dashboard")
    assert resp.status_code == 303


async def test_login_rate_limited(client, dashboard_auth):
    for _ in range(auth.MAX_ATTEMPTS):
        await _login(client, "incorrecta")
    resp = await _login(client, PASSWORD)  # even the right password is locked out
    assert "Demasiados intentos" in resp.text
    assert auth.COOKIE_NAME not in client.cookies


async def test_login_success_sets_cookie(client, dashboard_auth):
    resp = await _login(client)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    assert auth.COOKIE_NAME in client.cookies

    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "Resumen" in resp.text


async def test_tampered_cookie_redirects(client, dashboard_auth):
    # Well-formed, unexpired, but wrong signature.
    import time

    client.cookies.set(
        auth.COOKIE_NAME, f"{int(time.time()) + 1000}.{'0' * 64}", path="/dashboard"
    )
    resp = await client.get("/dashboard")
    assert resp.status_code == 303


async def test_expired_token_rejected(dashboard_auth):
    token = auth.make_session_token(now=0)  # expired long ago
    assert not auth.verify_session_token(token)
    assert auth.verify_session_token(auth.make_session_token())


# ── Overview page ────────────────────────────────────────────────────────
async def test_overview_empty_db(client, dashboard_auth):
    await _login(client)
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "Todavía no hay partidos registrados" in resp.text
    assert "Todavía no hay jugadores valorados" in resp.text


async def test_overview_with_data(client, dashboard_auth):
    await _seed()
    await _login(client)
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    text = resp.text

    # Recent matches: both games, the live one flagged.
    assert "Millonarios vs América" in text
    assert "Bogotá vs Valle" in text
    assert "En vivo" in text

    # Totals: 3 players (1 unidentified), 4 observations (1 team note).
    assert "(1 sin identificar)" in text
    assert "(1 de equipo)" in text

    # Top rated + auto-decision from the 1–5 rating.
    assert "Jordan Ferrin" in text
    assert "A firmar" in text  # rating 5
    assert "A seguir" in text  # rating 2


# ── Matches list ─────────────────────────────────────────────────────────
async def test_matches_requires_auth(client, dashboard_auth):
    resp = await client.get("/dashboard/partidos")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"


async def test_matches_list(client, dashboard_auth):
    await _seed()
    await _login(client)
    resp = await client.get("/dashboard/partidos")
    assert resp.status_code == 200
    text = resp.text
    assert "Millonarios vs América" in text
    assert "Bogotá vs Valle" in text
    assert "En vivo" in text
    assert "El Campín" in text
    assert "Wilmer" in text
    assert "20/06/2026" in text  # match_date rendered in the scout's timezone


async def test_matches_filters(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)

    resp = await client.get("/dashboard/partidos", params={"competicion": "Liga"})
    assert "Millonarios vs América" in resp.text
    assert "Bogotá vs Valle" not in resp.text

    resp = await client.get("/dashboard/partidos", params={"estado": "activo"})
    assert "Bogotá vs Valle" in resp.text
    assert "Millonarios vs América" not in resp.text

    # The old match is dated 2026-06-20; the live one was created "today".
    resp = await client.get("/dashboard/partidos", params={"hasta": "2026-06-30"})
    assert "Millonarios vs América" in resp.text
    assert "Bogotá vs Valle" not in resp.text

    resp = await client.get("/dashboard/partidos", params={"desde": "2026-07-01"})
    assert "Bogotá vs Valle" in resp.text
    assert "Millonarios vs América" not in resp.text

    # Nothing matches → empty state; malformed dates are ignored, not a 500.
    resp = await client.get(
        "/dashboard/partidos", params={"competicion": "Liga", "estado": "activo"}
    )
    assert "No hay partidos que coincidan" in resp.text
    resp = await client.get("/dashboard/partidos", params={"desde": "ayer"})
    assert resp.status_code == 200
    assert seeded  # (silence unused-variable linters)


# ── Match detail ─────────────────────────────────────────────────────────
async def test_match_detail(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get(f"/dashboard/partidos/{seeded['old'].id}")
    assert resp.status_code == 200
    text = resp.text

    # Header metadata.
    assert "Millonarios vs América" in text
    assert "Liga" in text and "El Campín" in text and "Wilmer" in text
    assert "Primer tiempo" in text

    # Timeline: chronological, with minutes, rating chip and substitution badge.
    assert text.index("Gran juego aéreo") < text.index("Lento en el repliegue")
    assert "12&#39;" in text or "12'" in text
    assert "5 / 5" in text
    assert "Cambio" in text and "Rodríguez" in text

    # Team notes live in their own section, not the timeline.
    assert "Notas de equipo" in text
    assert "Presión alta tras pérdida" in text


async def test_match_detail_dorsal_only_player(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get(f"/dashboard/partidos/{seeded['live'].id}")
    assert resp.status_code == 200
    assert "Dorsal 7" in resp.text
    assert "Rápido en el 1vs1" in resp.text


async def test_match_detail_not_found(client, dashboard_auth):
    await _login(client)
    resp = await client.get("/dashboard/partidos/9999")
    assert resp.status_code == 404
    assert "no existe" in resp.text
