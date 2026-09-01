"""Dashboard tests: auth flow (password → signed cookie) and the overview page.

Drives the real FastAPI app over httpx's ASGITransport (no lifespan — the
`storage` fixture owns Tortoise, same trick as the e2e harness). DASHBOARD
settings are injected per-test via the `dashboard_auth` fixture.
"""

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from scouting_bot.config import settings
from scouting_bot.dashboard import auth, queries
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
    """Configure the dashboard password for a test; restore afterwards. Also
    pins mock AI: the AI-summary routes resolve the provider from settings at
    call time, and tests must never depend on the host's .env (nor spend real
    API tokens) — the rest of the suite injects MockAIProvider explicitly."""
    old_pw = _set("dashboard_password", PASSWORD)
    old_secret = _set("dashboard_secret", "")
    old_mock = settings.use_mock_ai
    object.__setattr__(settings, "use_mock_ai", True)
    auth._attempts.clear()
    yield
    _set("dashboard_password", old_pw)
    _set("dashboard_secret", old_secret)
    object.__setattr__(settings, "use_mock_ai", old_mock)
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
        team="Millonarios", normalized_team="millonarios", latest_rating=5,
        position="Delantero",
    )
    ocampo = await Prospect.create(
        agent_chat_id=1, name="Ocampo", normalized_name="ocampo",
        team="América", normalized_team="america", latest_rating=2,
    )
    temp = await Prospect.create(
        agent_chat_id=1, name="", normalized_name="", team="Valle",
        normalized_team="valle", is_temporary=True,
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
    await Observation.create(
        session=live, prospect=ferrin, player_name="Jordan Ferrin",
        raw_quote="Buen cambio de ritmo", minute=70, rating=4,
    )
    return {"old": old, "live": live, "ferrin": ferrin, "ocampo": ocampo, "temp": temp}


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
    assert "Todavía no hay jugadores destacados" in resp.text


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


async def test_match_detail_links_players(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get(f"/dashboard/partidos/{seeded['old'].id}")
    assert f"/dashboard/jugadores/{seeded['ferrin'].id}" in resp.text


# ── Players list ─────────────────────────────────────────────────────────
async def test_players_requires_auth(client, dashboard_auth):
    resp = await client.get("/dashboard/jugadores")
    assert resp.status_code == 303


async def test_players_list(client, dashboard_auth):
    await _seed()
    await _login(client)
    resp = await client.get("/dashboard/jugadores")
    assert resp.status_code == 200
    text = resp.text

    assert "Jordan Ferrin" in text
    assert "Ocampo" in text
    assert "Sin identificar (dorsal 7)" in text  # temp prospect is never dropped
    assert "Delantero" in text
    assert "5 / 5" in text
    assert "A firmar" in text and "A seguir" in text

    # Sorted best-rated first, temps last.
    assert text.index("Jordan Ferrin") < text.index("Ocampo") < text.index("Sin identificar")


async def test_players_filters(client, dashboard_auth):
    await _seed()
    await _login(client)

    # Accent-insensitive search over name and team.
    resp = await client.get("/dashboard/jugadores", params={"q": "ferrin"})
    assert "Jordan Ferrin" in resp.text and "Ocampo" not in resp.text
    resp = await client.get("/dashboard/jugadores", params={"q": "america"})
    assert "Ocampo" in resp.text and "Jordan Ferrin" not in resp.text

    resp = await client.get("/dashboard/jugadores", params={"equipo": "Millonarios"})
    assert "Jordan Ferrin" in resp.text and "Ocampo" not in resp.text

    resp = await client.get("/dashboard/jugadores", params={"decision": "A firmar"})
    assert "Jordan Ferrin" in resp.text and "Ocampo" not in resp.text
    resp = await client.get("/dashboard/jugadores", params={"decision": "Sin valorar"})
    assert "Sin identificar" in resp.text and "Jordan Ferrin" not in resp.text

    resp = await client.get("/dashboard/jugadores", params={"valoracion_min": "3"})
    assert "Jordan Ferrin" in resp.text and "Ocampo" not in resp.text

    resp = await client.get("/dashboard/jugadores", params={"q": "nadie"})
    assert "No hay jugadores que coincidan" in resp.text
    resp = await client.get("/dashboard/jugadores", params={"valoracion_min": "muchas"})
    assert resp.status_code == 200  # malformed input is ignored, not a 500


# ── Player profile ───────────────────────────────────────────────────────
async def test_player_profile(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get(f"/dashboard/jugadores/{seeded['ferrin'].id}")
    assert resp.status_code == 200
    text = resp.text

    # Bio header.
    assert "Jordan Ferrin" in text
    assert "Millonarios" in text and "Delantero" in text
    assert "A firmar" in text

    # Observations grouped under both matches, with their quotes.
    assert "Millonarios vs América" in text and "Bogotá vs Valle" in text
    assert "Gran juego aéreo" in text and "Buen cambio de ritmo" in text

    # Rating history: one row per rated observation, linked to its match.
    assert "Historial de valoraciones" in text
    assert "5 / 5" in text and "4 / 5" in text
    assert f"/dashboard/partidos/{seeded['old'].id}" in text


async def test_player_profile_temporary(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get(f"/dashboard/jugadores/{seeded['temp'].id}")
    assert resp.status_code == 200
    assert "Sin identificar (dorsal 7)" in resp.text
    assert "Perfil sin identificar" in resp.text
    assert "Rápido en el 1vs1" in resp.text


async def test_player_not_found(client, dashboard_auth):
    await _login(client)
    resp = await client.get("/dashboard/jugadores/9999")
    assert resp.status_code == 404
    assert "no existe" in resp.text


# ── AI summary cache ─────────────────────────────────────────────────────
async def test_ai_summary_generated_on_first_view(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id

    resp = await client.get(f"/dashboard/jugadores/{pid}")
    assert resp.status_code == 200
    assert "Resumen IA" in resp.text
    assert "Recomendación" in resp.text  # mock provider's deterministic output

    p = await Prospect.get(id=pid)
    assert p.ai_summary
    assert p.ai_summary_obs_count == 2  # both Ferrin observations


async def test_ai_summary_served_from_cache(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    await client.get(f"/dashboard/jugadores/{pid}")  # generates + caches

    # Plant a sentinel: with an unchanged obs count it must be served verbatim,
    # with no regeneration and no refresh notice.
    await Prospect.filter(id=pid).update(ai_summary="RESUMEN CENTINELA")
    resp = await client.get(f"/dashboard/jugadores/{pid}")
    assert "RESUMEN CENTINELA" in resp.text
    assert "actualizando" not in resp.text
    p = await Prospect.get(id=pid)
    assert p.ai_summary == "RESUMEN CENTINELA"


async def test_ai_summary_stale_while_revalidate(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    await client.get(f"/dashboard/jugadores/{pid}")
    await Prospect.filter(id=pid).update(ai_summary="RESUMEN CENTINELA")

    # A new observation makes the watermark drift.
    await Observation.create(
        session=seeded["live"], prospect=seeded["ferrin"],
        player_name="Jordan Ferrin", raw_quote="Gol de cabeza", minute=80,
    )

    # The stale summary is served instantly, flagged as refreshing.
    resp = await client.get(f"/dashboard/jugadores/{pid}")
    assert "RESUMEN CENTINELA" in resp.text
    assert "actualizando" in resp.text

    # The background refresh ran after the response: cache is fresh now.
    p = await Prospect.get(id=pid)
    assert p.ai_summary != "RESUMEN CENTINELA"
    assert p.ai_summary_obs_count == 3

    resp = await client.get(f"/dashboard/jugadores/{pid}")
    assert "RESUMEN CENTINELA" not in resp.text
    assert "actualizando" not in resp.text


async def test_ai_summary_absent_without_observations(client, dashboard_auth):
    p = await Prospect.create(agent_chat_id=1, name="Nuevo", normalized_name="nuevo")
    await _login(client)
    resp = await client.get(f"/dashboard/jugadores/{p.id}")
    assert resp.status_code == 200
    assert "Resumen IA" not in resp.text


# ── Decision board ───────────────────────────────────────────────────────
async def test_decisions_requires_auth(client, dashboard_auth):
    resp = await client.get("/dashboard/decisiones")
    assert resp.status_code == 303


async def test_decision_board_groups(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get("/dashboard/decisiones")
    assert resp.status_code == 200
    text = resp.text

    # Groups appear best-first: A firmar (Ferrin, 5) → A seguir (Ocampo, 2)
    # → Sin valorar (the unnamed dorsal-7 profile).
    assert text.index("A firmar") < text.index("A seguir") < text.index("Sin valorar")

    # Each player sits in their group's card, linking to the profile.
    ferrin_pos = text.index("Jordan Ferrin")
    assert text.index("A firmar") < ferrin_pos < text.index("A seguir")
    assert f"/dashboard/jugadores/{seeded['ferrin'].id}" in text
    assert "Sin identificar (dorsal 7)" in text


async def test_decision_board_empty(client, dashboard_auth):
    await _login(client)
    resp = await client.get("/dashboard/decisiones")
    assert resp.status_code == 200
    assert "Todavía no hay jugadores registrados" in resp.text


# ── Player editing ───────────────────────────────────────────────────────
def _csrf(html: str) -> str:
    """Pull the CSRF token out of a rendered form (the browser's job)."""
    marker = f'name="{auth.CSRF_FIELD}" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


async def _edit_form(client: httpx.AsyncClient, pid: int) -> tuple[str, dict]:
    """Open the edit page and return (csrf, the form's current values)."""
    resp = await client.get(f"/dashboard/jugadores/{pid}/editar")
    assert resp.status_code == 200
    return _csrf(resp.text), {}


async def test_edit_requires_auth(client, dashboard_auth):
    seeded = await _seed()
    resp = await client.get(f"/dashboard/jugadores/{seeded['ferrin'].id}/editar")
    assert resp.status_code == 303


async def test_edit_page_prefills_current_values(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get(f"/dashboard/jugadores/{seeded['ferrin'].id}/editar")
    assert resp.status_code == 200
    text = resp.text
    assert 'value="Jordan Ferrin"' in text
    assert 'value="Millonarios"' in text
    # The stored free-text position is offered as an option, so opening and
    # saving the form never rewrites what the bot captured.
    assert '<option value="Delantero" selected>' in text
    assert "Delantero centro" in text  # canonical roles are offered too
    assert "Pie" in text and "Valor de mercado" in text


async def test_edit_saves_every_bio_field(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    csrf, _ = await _edit_form(client, pid)

    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={
            auth.CSRF_FIELD: csrf,
            "nombre": "Jordan Ferrin",
            "equipo": "Millonarios",
            "posicion": "Delantero centro",
            "dorsal": "9",
            "anio_nacimiento": "2008",
            "estatura": "187",
            "peso": "78",
            "pie": "izquierdo",
            "nacionalidad": "Colombia",
            "procedencia": "Academia Compensar",
            "valor": "$250.000",
            "contrato_hasta": "2028",
            "agente": "Carlos Gómez",
            "telefono_agente": "300 555 1234",
            "valoracion": "4.5",
            "decision": "",
            "notas": "Zurdo, buen remate de primera.",
        },
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/dashboard/jugadores/{pid}"

    p = await Prospect.get(id=pid)
    assert p.position == "Delantero centro"
    assert p.shirt_number == 9
    assert p.birth_year == 2008
    assert p.height_cm == 187 and p.weight_kg == 78
    assert p.preferred_foot == "izquierdo"
    assert p.nationality == "Colombia"
    assert p.origin_club == "Academia Compensar"
    assert p.market_value_usd == 250000  # «$250.000» is read as a number
    assert p.contract_year == 2028
    assert p.agent_name == "Carlos Gómez" and p.agent_phone == "300 555 1234"
    assert p.latest_rating == 4.5
    assert p.decision_status is None  # "automática" ⇒ derived from the rating
    assert p.notes == "Zurdo, buen remate de primera."

    # And the profile page shows them.
    resp = await client.get(f"/dashboard/jugadores/{pid}")
    text = resp.text
    assert "Muy interesante" in text  # 4.5 → auto-decision
    assert "$250 mil" in text
    assert "Izquierdo" in text and "Colombia" in text
    assert "Academia Compensar" in text and "Carlos Gómez" in text
    assert "18 años" in text or "17 años" in text  # derived from the birth year


async def test_edit_explicit_decision_overrides_rating(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ocampo"].id  # rating 2 ⇒ would derive "A seguir"
    csrf, _ = await _edit_form(client, pid)

    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={
            auth.CSRF_FIELD: csrf, "nombre": "Ocampo", "equipo": "América",
            "valoracion": "2", "decision": "Interesante",
        },
    )
    assert resp.status_code == 303
    p = await Prospect.get(id=pid)
    assert p.decision_status == "Interesante"
    resp = await client.get(f"/dashboard/jugadores/{pid}")
    assert "Interesante" in resp.text


async def test_edit_rejects_bad_values_without_saving(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    csrf, _ = await _edit_form(client, pid)

    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={
            auth.CSRF_FIELD: csrf, "nombre": "Jordan Ferrin",
            "anio_nacimiento": "108", "estatura": "1870", "dorsal": "0",
            "valoracion": "9",
        },
    )
    assert resp.status_code == 400
    text = resp.text
    assert "no se guardó nada" in text.lower()
    assert "El año de nacimiento" in text and "La estatura" in text

    p = await Prospect.get(id=pid)
    assert p.birth_year is None and p.height_cm is None
    assert p.name == "Jordan Ferrin"  # untouched


async def test_edit_requires_csrf(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={auth.CSRF_FIELD: "0" * 20, "nombre": "Hackeado"},
    )
    assert resp.status_code == 400
    assert (await Prospect.get(id=pid)).name == "Jordan Ferrin"


async def test_naming_a_temporary_profile_makes_it_permanent(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["temp"].id
    csrf, _ = await _edit_form(client, pid)

    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={auth.CSRF_FIELD: csrf, "nombre": "Édison Restrepo", "equipo": "Valle"},
    )
    assert resp.status_code == 303
    p = await Prospect.get(id=pid)
    assert p.name == "Édison Restrepo"
    assert p.is_temporary is False
    # Re-keyed exactly the way the bot would key it (accent-insensitive).
    assert p.normalized_name == "edison restrepo"
    assert p.normalized_team == "valle"
    # The observations it already had come with it.
    resp = await client.get(f"/dashboard/jugadores/{pid}")
    assert "Édison Restrepo" in resp.text and "Rápido en el 1vs1" in resp.text


async def test_saving_a_temporary_profile_unnamed_keeps_its_key(client, dashboard_auth):
    """Blanking a name must not destroy the synthetic key the bot looks numbers
    up by — the profile just stays unidentified."""
    seeded = await _seed()
    await _login(client)
    pid = seeded["temp"].id
    before = await Prospect.get(id=pid)
    csrf, _ = await _edit_form(client, pid)

    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={auth.CSRF_FIELD: csrf, "nombre": "", "equipo": "Valle", "estatura": "175"},
    )
    assert resp.status_code == 303
    p = await Prospect.get(id=pid)
    assert p.normalized_name == before.normalized_name
    assert p.is_temporary is True
    assert p.height_cm == 175  # the rest of the edit still landed


async def test_rename_into_existing_player_offers_merge(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ocampo"].id
    csrf, _ = await _edit_form(client, pid)

    # Rename Ocampo into Ferrin's exact identity (same name and team).
    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={
            auth.CSRF_FIELD: csrf, "nombre": "Jordan Ferrin", "equipo": "Millonarios",
        },
    )
    assert resp.status_code == 409
    assert "Ya existe un jugador" in resp.text
    assert f"/dashboard/jugadores/{pid}/fusionar?con={seeded['ferrin'].id}" in resp.text
    # Nothing was written: one person must not end up as two records.
    assert (await Prospect.get(id=pid)).name == "Ocampo"


async def test_edit_not_found(client, dashboard_auth):
    await _login(client)
    resp = await client.get("/dashboard/jugadores/9999/editar")
    assert resp.status_code == 404


# ── Merging profiles ─────────────────────────────────────────────────────
async def test_merge_page_ranks_candidates(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get(f"/dashboard/jugadores/{seeded['ferrin'].id}/fusionar")
    assert resp.status_code == 200
    text = resp.text
    assert "Se conserva" in text
    assert "Ocampo" in text and "Sin identificar (dorsal 7)" in text
    assert "No se puede deshacer" in text


async def test_merge_moves_observations_and_backfills_bio(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    keep, drop = seeded["ferrin"], seeded["temp"]
    # The profile being dropped knows something the survivor doesn't.
    await Prospect.filter(id=drop.id).update(height_cm=181, preferred_foot="derecho")

    resp = await client.get(f"/dashboard/jugadores/{keep.id}/fusionar?con={drop.id}")
    assert resp.status_code == 200
    assert "Vas a fusionar con" in resp.text
    csrf = _csrf(resp.text)

    resp = await client.post(
        f"/dashboard/jugadores/{keep.id}/fusionar",
        data={auth.CSRF_FIELD: csrf, "con": str(drop.id)},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/dashboard/jugadores/{keep.id}"

    assert await Prospect.filter(id=drop.id).first() is None
    survivor = await Prospect.get(id=keep.id)
    assert survivor.name == "Jordan Ferrin"
    assert survivor.height_cm == 181  # backfilled from the dropped profile
    assert survivor.preferred_foot == "derecho"
    assert survivor.latest_rating == 5  # the survivor's own value is never clobbered
    # The dropped profile's observation now belongs to the survivor.
    assert await Observation.filter(prospect_id=keep.id).count() == 3


async def test_merge_requires_csrf(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.post(
        f"/dashboard/jugadores/{seeded['ferrin'].id}/fusionar",
        data={auth.CSRF_FIELD: "0" * 20, "con": str(seeded['temp'].id)},
    )
    assert resp.status_code == 400
    assert await Prospect.filter(id=seeded["temp"].id).first() is not None


async def test_merge_with_missing_profile_is_rejected(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    resp = await client.get(f"/dashboard/jugadores/{pid}/fusionar")
    csrf = _csrf(resp.text)
    resp = await client.post(
        f"/dashboard/jugadores/{pid}/fusionar",
        data={auth.CSRF_FIELD: csrf, "con": "9999"},
    )
    assert resp.status_code == 404
    assert await Prospect.filter(id=pid).first() is not None


# ── Home screen: featured players ────────────────────────────────────────
async def test_overview_leads_with_featured_players(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    text = resp.text

    # The headline is the players to act on, not the counters.
    assert text.index("Jugadores destacados") < text.index("Últimos partidos")
    assert text.index("Jordan Ferrin") < text.index("stat-strip")

    # Ferrin (5) is featured; Ocampo (2 → "A seguir") is not card-worthy.
    ferrin_link = f'/dashboard/jugadores/{seeded["ferrin"].id}"'
    assert ferrin_link in text
    assert f'class="player-card" href="/dashboard/jugadores/{seeded["ocampo"].id}"' not in text
    # …but every decision still shows as a count.
    assert "A seguir" in text


async def test_featured_players_share_one_list_ordered_best_first(client, dashboard_auth):
    """All the act-on-this-player decisions live in a single section, ordered by
    decision and then by rating, with each card carrying its own label."""
    await _seed()
    # A second player one decision down.
    await Prospect.create(
        agent_chat_id=1, name="Luis Mina", normalized_name="luis mina",
        team="Millonarios", normalized_team="millonarios", latest_rating=4,
    )
    await _login(client)
    text = (await client.get("/dashboard")).text
    assert text.index("A firmar") < text.index("Muy interesante")
    assert text.index("Jordan Ferrin") < text.index("Luis Mina")
    # One grid, not one per decision.
    assert text.count('class="player-cards"') == 1


async def test_manual_advance_status_is_featured(client, dashboard_auth):
    """A scout's explicit /decision "Avanzar" call is as actionable as a top
    rating, even without one — it must surface as its own card, not just a
    count in the decision strip."""
    await _seed()
    advancing = await Prospect.create(
        agent_chat_id=1, name="Edwin Rodríguez", normalized_name="edwin rodriguez",
        team="América", normalized_team="america", decision_status="Avanzar",
    )
    await _login(client)
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    text = resp.text
    assert "Avanzar" in text and "Edwin Rodríguez" in text
    # Order: A firmar (Ferrin, rating 5), then the manual Avanzar call. Both sit
    # in the one featured list, each card labelled with its own decision.
    assert text.index("A firmar") < text.index("Avanzar")
    assert text.count('class="player-cards"') == 1
    assert advancing  # (silence unused-variable linters)


async def test_watch_and_pending_statuses_stay_off_the_home_screen(client, dashboard_auth):
    await _seed()
    await Prospect.create(
        agent_chat_id=1, name="En Espera", normalized_name="en espera",
        decision_status="Pendiente",
    )
    await Prospect.create(
        agent_chat_id=1, name="Aun Mirando", normalized_name="aun mirando",
        decision_status="Seguir observando",
    )
    await _login(client)
    text = (await client.get("/dashboard")).text
    assert "En Espera" not in text
    assert "Aun Mirando" not in text
    # They're still visible in the full decision strip below the cards.
    assert "Pendiente" in text and "Seguir observando" in text


async def test_featured_card_shows_scouting_signals(client, dashboard_auth):
    seeded = await _seed()
    await Prospect.filter(id=seeded["ferrin"].id).update(
        position="Delantero centro", birth_year=2008, preferred_foot="izquierdo",
        market_value_usd=250000,
    )
    await _login(client)
    text = (await client.get("/dashboard")).text

    assert "DC" in text  # position badge from the taxonomy
    assert "Pie izquierdo" in text
    assert "$250 mil" in text
    assert "JF" in text  # initials placeholder (no photo stored)
    # Ferrin was rated 5 then 4 across two matches → the trend points down.
    assert "trend down" in text
    assert "solo 1 partido" not in text  # he has two


async def test_single_match_players_are_flagged(client, dashboard_auth):
    await _seed()
    solo = await Prospect.create(
        agent_chat_id=1, name="Kevin Salas", normalized_name="kevin salas",
        team="Valle", normalized_team="valle", latest_rating=5,
    )
    session = await Session.create(agent_chat_id=1, home_team="X", away_team="Y")
    await Observation.create(
        session=session, prospect=solo, player_name="Kevin Salas",
        raw_quote="Definición limpia", rating=5,
    )
    await _login(client)
    text = (await client.get("/dashboard")).text
    assert "solo 1 partido" in text


async def test_overview_without_featured_players(client, dashboard_auth):
    await Prospect.create(
        agent_chat_id=1, name="Bajo", normalized_name="bajo", latest_rating=1,
    )
    await _login(client)
    text = (await client.get("/dashboard")).text
    assert "Todavía no hay jugadores destacados" in text


# ── Player photo proxy ───────────────────────────────────────────────────
async def test_photo_requires_auth(client, dashboard_auth):
    seeded = await _seed()
    resp = await client.get(f"/dashboard/foto/{seeded['ferrin'].id}")
    assert resp.status_code == 303


async def test_photo_404_without_a_stored_file(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.get(f"/dashboard/foto/{seeded['ferrin'].id}")
    assert resp.status_code == 404
    resp = await client.get("/dashboard/foto/9999")
    assert resp.status_code == 404


async def test_photo_served_from_cache_without_calling_telegram(client, dashboard_auth):
    """A cached file_id is served straight from memory — no token, no network."""
    from scouting_bot.dashboard import photos

    seeded = await _seed()
    await Prospect.filter(id=seeded["ferrin"].id).update(photo_file_id="ph_l")
    photos.clear_cache()
    photos._cache["ph_l"] = (b"\xff\xd8fakejpeg", "image/jpeg")
    try:
        await _login(client)
        resp = await client.get(f"/dashboard/foto/{seeded['ferrin'].id}")
        assert resp.status_code == 200
        assert resp.content == b"\xff\xd8fakejpeg"
        assert resp.headers["content-type"].startswith("image/jpeg")
    finally:
        photos.clear_cache()


async def test_photo_404_when_telegram_is_unreachable(client, dashboard_auth):
    """An unavailable photo must not break the page — the card falls back to
    initials, so the proxy just 404s."""
    from scouting_bot.dashboard import photos

    seeded = await _seed()
    await Prospect.filter(id=seeded["ferrin"].id).update(photo_file_id="ph_missing")
    photos.clear_cache()
    old = _set("telegram_bot_token", "")  # no token ⇒ nothing to fetch with
    try:
        await _login(client)
        resp = await client.get(f"/dashboard/foto/{seeded['ferrin'].id}")
        assert resp.status_code == 404
        # The home screen still renders, with the photo slot pointing at us.
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert f'/dashboard/foto/{seeded["ferrin"].id}' in resp.text
    finally:
        _set("telegram_bot_token", old)
        photos.clear_cache()


# ── Filters and grouping ─────────────────────────────────────────────────
async def _seed_squad() -> dict:
    """Four players with full bios across positions, ages and feet."""
    session = await Session.create(agent_chat_id=1, home_team="Cali", away_team="Pasto")
    made = {}
    for key, name, position, birth_year, foot, nationality, value, rating in (
        ("gk", "Andrés Mesa", "Portero", 2004, "derecho", "Colombia", 80000, 3),
        ("cb", "Luis Mina", "Defensa central", 2008, "izquierdo", "Colombia", 150000, 4),
        ("lb", "Juan Caro", "lateral", 2010, "izquierdo", "Venezuela", None, 4),
        ("st", "Kevin Salas", "Delantero centro", 2000, "derecho", "Colombia", 900000, 5),
    ):
        p = await Prospect.create(
            agent_chat_id=1, name=name, normalized_name=name.lower(),
            team="Cali", normalized_team="cali", position=position,
            birth_year=birth_year, preferred_foot=foot, nationality=nationality,
            market_value_usd=value, latest_rating=rating,
        )
        await Observation.create(
            session=session, prospect=p, player_name=name,
            raw_quote=f"Nota de {name}", rating=rating,
        )
        made[key] = p
    return made


async def test_players_filter_by_position_role_and_line(client, dashboard_auth):
    await _seed_squad()
    await _login(client)

    # A specific role.
    resp = await client.get("/dashboard/jugadores", params={"posicion": "Defensa central"})
    assert "Luis Mina" in resp.text
    assert "Juan Caro" not in resp.text and "Kevin Salas" not in resp.text

    # A whole line — "lateral" has no side, but it is still a defender.
    resp = await client.get("/dashboard/jugadores", params={"posicion": "Defensa"})
    assert "Luis Mina" in resp.text and "Juan Caro" in resp.text
    assert "Kevin Salas" not in resp.text

    # The dropdown offers the line and the roles found under it.
    assert "Defensa (toda la línea)" in resp.text
    assert "Delantero centro" in resp.text


async def test_players_filter_by_age_bucket(client, dashboard_auth):
    await _seed_squad()
    await _login(client)

    resp = await client.get("/dashboard/jugadores", params={"edad": "sub17"})
    assert "Juan Caro" in resp.text  # 2010
    assert "Luis Mina" not in resp.text

    # Brackets are inclusive the way football uses them: sub-20 contains sub-17.
    resp = await client.get("/dashboard/jugadores", params={"edad": "sub20"})
    assert "Juan Caro" in resp.text and "Luis Mina" in resp.text
    assert "Kevin Salas" not in resp.text

    resp = await client.get("/dashboard/jugadores", params={"edad": "mayores"})
    assert "Kevin Salas" in resp.text and "Juan Caro" not in resp.text


async def test_players_filter_by_foot_and_nationality(client, dashboard_auth):
    await _seed_squad()
    await _login(client)

    resp = await client.get("/dashboard/jugadores", params={"pie": "izquierdo"})
    assert "Luis Mina" in resp.text and "Juan Caro" in resp.text
    assert "Kevin Salas" not in resp.text

    resp = await client.get("/dashboard/jugadores", params={"nacionalidad": "Venezuela"})
    assert "Juan Caro" in resp.text and "Luis Mina" not in resp.text


async def test_players_filters_combine(client, dashboard_auth):
    await _seed_squad()
    await _login(client)
    resp = await client.get(
        "/dashboard/jugadores",
        params={"posicion": "Defensa", "edad": "sub20", "pie": "izquierdo"},
    )
    assert "Luis Mina" in resp.text and "Juan Caro" in resp.text
    assert "Kevin Salas" not in resp.text and "Andrés Mesa" not in resp.text


async def test_players_sorting(client, dashboard_auth):
    await _seed_squad()
    await _login(client)

    text = (await client.get("/dashboard/jugadores", params={"orden": "edad"})).text
    assert text.index("Juan Caro") < text.index("Luis Mina") < text.index("Kevin Salas")

    text = (await client.get("/dashboard/jugadores", params={"orden": "valor"})).text
    assert text.index("Kevin Salas") < text.index("Luis Mina") < text.index("Andrés Mesa")

    text = (await client.get("/dashboard/jugadores", params={"orden": "nombre"})).text
    assert text.index("Andrés Mesa") < text.index("Juan Caro") < text.index("Kevin Salas")

    # Default stays best-rated first.
    text = (await client.get("/dashboard/jugadores")).text
    assert text.index("Kevin Salas") < text.index("Andrés Mesa")


async def test_players_unknown_age_excluded_from_brackets(client, dashboard_auth):
    await _seed_squad()
    await Prospect.create(
        agent_chat_id=1, name="Sin Datos", normalized_name="sin datos", team="Cali",
        normalized_team="cali", latest_rating=4,
    )
    await _login(client)
    resp = await client.get("/dashboard/jugadores", params={"edad": "sub23"})
    assert "Sin Datos" not in resp.text  # can't be claimed for a bracket
    resp = await client.get("/dashboard/jugadores")
    assert "Sin Datos" in resp.text


async def test_players_list_shows_scouting_columns(client, dashboard_auth):
    await _seed_squad()
    await _login(client)
    text = (await client.get("/dashboard/jugadores")).text
    assert "DC" in text and "POR" in text  # position badges
    assert "$900 mil" in text
    assert "Izquierdo" in text


async def test_decision_board_groups_by_position(client, dashboard_auth):
    await _seed_squad()
    await _login(client)
    resp = await client.get("/dashboard/decisiones")
    assert resp.status_code == 200
    text = resp.text

    # Within "Muy interesante" (rating 4) the two defenders sit under Defensa.
    assert "Muy interesante" in text
    assert text.index("A firmar") < text.index("Muy interesante")
    # Position headings appear in pitch order within a tier.
    assert "Defensa" in text and "Delantero" in text


async def test_decision_board_filters(client, dashboard_auth):
    await _seed_squad()
    await _login(client)

    resp = await client.get("/dashboard/decisiones", params={"posicion": "Defensa"})
    assert "Luis Mina" in resp.text and "Juan Caro" in resp.text
    assert "Kevin Salas" not in resp.text

    resp = await client.get("/dashboard/decisiones", params={"edad": "sub17"})
    assert "Juan Caro" in resp.text and "Kevin Salas" not in resp.text

    resp = await client.get(
        "/dashboard/decisiones", params={"posicion": "Portero", "edad": "sub17"}
    )
    assert "No hay jugadores que coincidan" in resp.text


async def test_filters_only_offer_values_present_in_the_data(client, dashboard_auth):
    await _seed_squad()
    await _login(client)
    text = (await client.get("/dashboard/jugadores")).text
    # Nobody is ambidextrous or a Pivote, so neither is offered.
    assert 'value="ambidiestro"' not in text
    assert ">&nbsp;&nbsp;Pivote<" not in text
    assert 'value="Venezuela"' in text


# ── Creating a player from scratch ───────────────────────────────────────
async def _new_form(client: httpx.AsyncClient) -> str:
    """Open the create page and return its CSRF token (the browser's job)."""
    resp = await client.get("/dashboard/jugadores/nuevo")
    assert resp.status_code == 200
    return _csrf(resp.text)


async def test_new_player_requires_auth(client, dashboard_auth):
    resp = await client.get("/dashboard/jugadores/nuevo")
    assert resp.status_code == 303


async def test_players_list_offers_the_create_button(client, dashboard_auth):
    await _seed()
    await _login(client)
    text = (await client.get("/dashboard/jugadores")).text
    assert 'href="/dashboard/jugadores/nuevo"' in text
    assert "Nuevo jugador" in text


async def test_new_player_page_renders_the_empty_form(client, dashboard_auth):
    await _login(client)
    resp = await client.get("/dashboard/jugadores/nuevo")
    assert resp.status_code == 200
    text = resp.text
    assert "Nuevo jugador" in text and "Crear jugador" in text
    assert 'action="/dashboard/jugadores/nuevo"' in text
    # The same option lists the edit form offers.
    assert "Delantero centro" in text and "Valor de mercado" in text


async def test_create_player_saves_and_opens_the_profile(client, dashboard_auth):
    await _seed()
    await _login(client)
    csrf = await _new_form(client)

    resp = await client.post(
        "/dashboard/jugadores/nuevo",
        data={
            auth.CSRF_FIELD: csrf,
            "nombre": "Andrés Mosquera",
            "equipo": "Cali",
            "posicion": "Lateral izquierdo",
            "dorsal": "3",
            "anio_nacimiento": "2007",
            "estatura": "175",
            "pie": "izquierdo",
            "nacionalidad": "Colombia",
            "procedencia": "Academia Cali",
            "valor": "$120.000",
            "valoracion": "4",
            "decision": "",
            "notas": "Recomendado por un contacto; aún sin ver en vivo.",
        },
    )
    assert resp.status_code == 303
    p = await Prospect.get(name="Andrés Mosquera")
    assert resp.headers["location"] == f"/dashboard/jugadores/{p.id}"
    assert p.team == "Cali" and p.position == "Lateral izquierdo"
    assert p.shirt_number == 3 and p.birth_year == 2007 and p.height_cm == 175
    assert p.preferred_foot == "izquierdo" and p.nationality == "Colombia"
    assert p.market_value_usd == 120000
    assert p.latest_rating == 4 and p.decision_status is None  # derived
    assert p.is_temporary is False
    # Keyed exactly as the bot keys names, so a later note lands on this record.
    assert p.normalized_name == "andres mosquera" and p.normalized_team == "cali"

    # The profile page opens with no observations yet.
    text = (await client.get(f"/dashboard/jugadores/{p.id}")).text
    assert "Andrés Mosquera" in text
    assert "Muy interesante" in text  # 4 → auto-decision
    assert "Sin observaciones registradas." in text

    # And it shows up in the list.
    assert "Andrés Mosquera" in (await client.get("/dashboard/jugadores")).text


async def test_created_player_is_the_record_the_bot_resolves(client, dashboard_auth, storage):
    """A player typed into the panel and one the bot hears about are one record."""
    seeded = await _seed()  # sessions belong to chat 1
    await _login(client)
    csrf = await _new_form(client)
    resp = await client.post(
        "/dashboard/jugadores/nuevo",
        data={auth.CSRF_FIELD: csrf, "nombre": "Camilo Reyes", "equipo": "Millonarios"},
    )
    assert resp.status_code == 303
    created = await Prospect.get(name="Camilo Reyes")
    assert created.agent_chat_id == seeded["old"].agent_chat_id

    # The bot resolving that same name+team lands on the record, not a second one.
    resolved = await storage.get_or_create_prospect(1, "Camilo Reyes", "Millonarios")
    assert resolved.id == created.id
    assert await Prospect.filter(normalized_name="camilo reyes").count() == 1


async def test_create_player_requires_a_name(client, dashboard_auth):
    await _login(client)
    csrf = await _new_form(client)
    before = await Prospect.all().count()
    resp = await client.post(
        "/dashboard/jugadores/nuevo",
        data={auth.CSRF_FIELD: csrf, "nombre": "  ", "equipo": "Cali"},
    )
    assert resp.status_code == 400
    assert "El nombre es obligatorio." in resp.text
    assert 'value="Cali"' in resp.text  # what was typed survives the round trip
    assert await Prospect.all().count() == before


async def test_create_player_rejects_bad_values_without_saving(client, dashboard_auth):
    await _login(client)
    csrf = await _new_form(client)
    resp = await client.post(
        "/dashboard/jugadores/nuevo",
        data={auth.CSRF_FIELD: csrf, "nombre": "Test", "edad": "180"},
    )
    assert resp.status_code == 400
    assert "La edad debe estar entre 10 y 60." in resp.text
    assert await Prospect.filter(name="Test").count() == 0


async def test_create_player_detects_an_existing_identity(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    csrf = await _new_form(client)
    resp = await client.post(
        "/dashboard/jugadores/nuevo",
        data={auth.CSRF_FIELD: csrf, "nombre": "Jordan Ferrin", "equipo": "Millonarios"},
    )
    assert resp.status_code == 409
    assert "Ya existe un jugador con ese nombre y equipo" in resp.text
    assert f'/dashboard/jugadores/{seeded["ferrin"].id}' in resp.text
    assert await Prospect.filter(normalized_name="jordan ferrin").count() == 1


async def test_create_player_requires_csrf(client, dashboard_auth):
    await _login(client)
    resp = await client.post(
        "/dashboard/jugadores/nuevo", data={auth.CSRF_FIELD: "falso", "nombre": "X"}
    )
    assert resp.status_code == 400
    assert await Prospect.filter(name="X").count() == 0


# ── Team category derived from the team name ─────────────────────────────
async def test_create_player_splits_the_team_category(client, dashboard_auth):
    await _login(client)
    csrf = await _new_form(client)
    resp = await client.post(
        "/dashboard/jugadores/nuevo",
        data={auth.CSRF_FIELD: csrf, "nombre": "Andrés Mosquera", "equipo": "Santa Fe U18"},
    )
    assert resp.status_code == 303
    p = await Prospect.get(name="Andrés Mosquera")
    assert p.team == "Santa Fe" and p.category == "Sub-18"
    assert p.normalized_team == "santa fe"


async def test_edit_team_rename_splits_the_category(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ocampo"].id
    csrf, _ = await _edit_form(client, pid)
    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={auth.CSRF_FIELD: csrf, "nombre": "Ocampo", "equipo": "América Sub-20"},
    )
    assert resp.status_code == 303
    p = await Prospect.get(id=pid)
    assert p.team == "América" and p.category == "Sub-20"


async def test_edit_without_a_category_keeps_the_stored_one(client, dashboard_auth):
    """The form has no category field, so saving it must not wipe the value."""
    seeded = await _seed()
    await Prospect.filter(id=seeded["ferrin"].id).update(category="Sub-18")
    await _login(client)
    pid = seeded["ferrin"].id
    csrf, _ = await _edit_form(client, pid)
    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={auth.CSRF_FIELD: csrf, "nombre": "Jordan Ferrin", "equipo": "Millonarios"},
    )
    assert resp.status_code == 303
    p = await Prospect.get(id=pid)
    assert p.team == "Millonarios" and p.category == "Sub-18"


async def test_creating_a_player_of_a_category_team_hits_the_existing_record(
    client, dashboard_auth
):
    """"Jordan Ferrin / Millonarios U18" is the seeded "Millonarios" player."""
    seeded = await _seed()
    await _login(client)
    csrf = await _new_form(client)
    resp = await client.post(
        "/dashboard/jugadores/nuevo",
        data={auth.CSRF_FIELD: csrf, "nombre": "Jordan Ferrin", "equipo": "Millonarios U18"},
    )
    assert resp.status_code == 409
    assert f'/dashboard/jugadores/{seeded["ferrin"].id}' in resp.text


# ── Contact follow-up (CRM) ──────────────────────────────────────────────
async def test_profile_shows_the_contact_section(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    text = (await client.get(f"/dashboard/jugadores/{seeded['ferrin'].id}")).text
    assert "Seguimiento" in text
    assert "Sin contactar" in text  # no status stored ⇒ never contacted
    assert 'action="/dashboard/jugadores/%d/contacto"' % seeded["ferrin"].id in text
    assert "En conversación" in text  # the one-click buttons


async def test_contact_button_sets_status_and_stamps_today(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    csrf = _csrf((await client.get(f"/dashboard/jugadores/{pid}")).text)

    resp = await client.post(
        f"/dashboard/jugadores/{pid}/contacto",
        data={auth.CSRF_FIELD: csrf, "estado": "Contactado"},
    )
    assert resp.status_code == 303
    p = await Prospect.get(id=pid)
    assert p.contact_status == "Contactado"
    assert p.last_contact_at == datetime.now(queries.TZ).date()

    text = (await client.get(f"/dashboard/jugadores/{pid}")).text
    assert "Contactado" in text and "Último contacto" in text


async def test_contact_button_back_to_none_clears_the_date(client, dashboard_auth):
    seeded = await _seed()
    await Prospect.filter(id=seeded["ocampo"].id).update(
        contact_status="Acuerdo", last_contact_at=date(2026, 8, 1)
    )
    await _login(client)
    pid = seeded["ocampo"].id
    csrf = _csrf((await client.get(f"/dashboard/jugadores/{pid}")).text)

    resp = await client.post(
        f"/dashboard/jugadores/{pid}/contacto",
        data={auth.CSRF_FIELD: csrf, "estado": "Sin contactar"},
    )
    assert resp.status_code == 303
    p = await Prospect.get(id=pid)
    assert p.contact_status is None and p.last_contact_at is None


async def test_contact_button_rejects_an_unknown_status(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    csrf = _csrf((await client.get(f"/dashboard/jugadores/{pid}")).text)
    resp = await client.post(
        f"/dashboard/jugadores/{pid}/contacto",
        data={auth.CSRF_FIELD: csrf, "estado": "Fichado ya"},
    )
    assert resp.status_code == 400
    assert (await Prospect.get(id=pid)).contact_status is None


async def test_contact_button_requires_csrf(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    resp = await client.post(
        f"/dashboard/jugadores/{seeded['ferrin'].id}/contacto",
        data={auth.CSRF_FIELD: "falso", "estado": "Contactado"},
    )
    assert resp.status_code == 400


async def test_edit_form_saves_the_contact_fields(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    csrf, _ = await _edit_form(client, pid)
    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={
            auth.CSRF_FIELD: csrf,
            "nombre": "Jordan Ferrin",
            "equipo": "Millonarios",
            "estado_contacto": "Reunión agendada",
            "fecha_contacto": "2026-08-20",
            "notas_contacto": "Hablé con el agente; reunión el viernes.",
        },
    )
    assert resp.status_code == 303
    p = await Prospect.get(id=pid)
    assert p.contact_status == "Reunión agendada"
    assert p.last_contact_at == date(2026, 8, 20)
    assert p.contact_notes == "Hablé con el agente; reunión el viernes."

    text = (await client.get(f"/dashboard/jugadores/{pid}")).text
    assert "Reunión agendada" in text and "20/08/2026" in text
    assert "reunión el viernes" in text


async def test_edit_form_rejects_a_future_contact_date(client, dashboard_auth):
    seeded = await _seed()
    await _login(client)
    pid = seeded["ferrin"].id
    csrf, _ = await _edit_form(client, pid)
    future = (date.today() + timedelta(days=3)).isoformat()
    resp = await client.post(
        f"/dashboard/jugadores/{pid}/editar",
        data={
            auth.CSRF_FIELD: csrf, "nombre": "Jordan Ferrin", "equipo": "Millonarios",
            "fecha_contacto": future,
        },
    )
    assert resp.status_code == 400
    assert "no puede ser futura" in resp.text
    assert (await Prospect.get(id=pid)).last_contact_at is None


async def test_create_player_with_a_contact_status(client, dashboard_auth):
    await _login(client)
    csrf = await _new_form(client)
    resp = await client.post(
        "/dashboard/jugadores/nuevo",
        data={
            auth.CSRF_FIELD: csrf, "nombre": "Camilo Reyes", "equipo": "Cali",
            "estado_contacto": "Contactado", "fecha_contacto": "2026-08-15",
        },
    )
    assert resp.status_code == 303
    p = await Prospect.get(name="Camilo Reyes")
    assert p.contact_status == "Contactado" and p.last_contact_at == date(2026, 8, 15)


async def test_players_list_shows_and_filters_by_contact(client, dashboard_auth):
    seeded = await _seed()
    await Prospect.filter(id=seeded["ferrin"].id).update(
        contact_status="En conversación", last_contact_at=date(2026, 8, 10)
    )
    await _login(client)

    text = (await client.get("/dashboard/jugadores")).text
    assert "Contacto" in text and "En conversación" in text and "10/08/2026" in text

    resp = await client.get("/dashboard/jugadores", params={"contacto": "En conversación"})
    assert "Jordan Ferrin" in resp.text and "Ocampo" not in resp.text

    resp = await client.get("/dashboard/jugadores", params={"contacto": "Sin contactar"})
    assert "Ocampo" in resp.text and "Jordan Ferrin" not in resp.text

    # A status nobody is in is not offered as a filter.
    assert 'value="Acuerdo"' not in text
