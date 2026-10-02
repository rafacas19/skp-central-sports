"""Matches created on the dashboard: creation, the bot guard, lineups, the pitch."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from scouting_bot.config import settings
from scouting_bot.dashboard import auth
from scouting_bot.dashboard.pitch import short_name
from scouting_bot.formations import FORMATIONS, pitch_point
from scouting_bot.models import (
    ORIGIN_WEB,
    Evaluation,
    MatchPlayer,
    Observation,
    Prospect,
    Session,
)
from scouting_bot.taxonomy import normalize_identity, normalize_name

PASSWORD = "prueba-scouting"


def _set(field: str, value) -> object:
    old = getattr(settings, field)
    object.__setattr__(settings, field, value)
    return old


@pytest_asyncio.fixture
async def client(storage):
    from scouting_bot.app import app

    old = {f: _set(f, v) for f, v in
           [("dashboard_password", PASSWORD), ("dashboard_secret", ""), ("use_mock_ai", True)]}
    auth._attempts.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/dashboard/login", data={"password": PASSWORD})
        yield c
    for f, v in old.items():
        _set(f, v)
    auth._attempts.clear()


def _csrf(html: str) -> str:
    marker = f'name="{auth.CSRF_FIELD}" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


async def _token(client) -> str:
    return _csrf((await client.get("/dashboard/partidos/nuevo")).text)


async def _new_match(client, **fields) -> httpx.Response:
    data = {auth.CSRF_FIELD: await _token(client), "local": "Santa Fe U18",
            "visitante": "Millonarios U18", "fecha": "2026-09-27", "competicion": "Liga"}
    data.update(fields)
    return await client.post("/dashboard/partidos/nuevo", data=data)


# ── Creating a match ─────────────────────────────────────────────────────
async def test_new_match_is_a_web_match_with_the_category_split(client):
    resp = await _new_match(client)
    assert resp.status_code == 303
    session = await Session.get()
    assert resp.headers["location"] == f"/dashboard/partidos/{session.id}/sistema?nuevo=1"
    assert (session.origin, session.state) == (ORIGIN_WEB, "active")
    assert (session.home_team, session.home_team_category) == ("Santa Fe", "Sub-18")
    assert (session.away_team, session.away_team_category) == ("Millonarios", "Sub-18")
    assert session.match_date.astimezone(timezone(timedelta(hours=-5))).date().isoformat() == "2026-09-27"
    assert "Santa Fe vs Millonarios" in (await client.get("/dashboard/partidos")).text


async def test_new_match_validates_the_form(client):
    resp = await _new_match(client, local="", visitante="Millonarios")
    assert resp.status_code == 400 and "El equipo local es obligatorio" in resp.text
    resp = await _new_match(client, local="Junior", visitante="junior")
    assert resp.status_code == 400 and "no pueden ser el mismo" in resp.text
    resp = await _new_match(client, fecha="27/09/2026")
    assert resp.status_code == 400 and "Fecha no válida" in resp.text
    assert await Session.all().count() == 0


async def test_matches_page_offers_a_new_match(client):
    assert "/dashboard/partidos/nuevo" in (await client.get("/dashboard/partidos")).text


# ── The bot never picks up a web match ───────────────────────────────────
async def test_web_match_is_invisible_to_the_bot(storage):
    bot = await storage.create_session(7, "Junior", "Nacional", None)
    web = await storage.create_session(7, "América", "Cali", None, origin=ORIGIN_WEB)
    assert web.id > bot.id
    assert (await storage.get_active_session(7)).id == bot.id

    stale = datetime.now(timezone.utc) - timedelta(hours=10)
    await Session.all().update(last_activity_at=stale)
    ids = {s.id for s in await storage.stale_active_sessions(datetime.now(timezone.utc))}
    assert ids == {bot.id}


async def test_with_only_a_web_match_the_bot_has_no_active_match(storage):
    await storage.create_session(7, "América", "Cali", None, origin=ORIGIN_WEB)
    assert await storage.get_active_session(7) is None


# ── Lineups ──────────────────────────────────────────────────────────────
async def _web_match(client) -> Session:
    await _new_match(client, local="Junior", visitante="Nacional")
    return await Session.get()


async def test_lineup_reuses_known_players_and_makes_number_only_ones_temporary(client):
    match = await _web_match(client)
    known = await Prospect.create(
        agent_chat_id=match.agent_chat_id, name="Camilo Restrepo",
        normalized_name=normalize_identity("Camilo Restrepo"),
        team="Junior", normalized_team=normalize_name("Junior"),
    )
    page = await client.get(f"/dashboard/partidos/{match.id}/alineacion")
    assert '<option value="Camilo Restrepo">' in page.text  # suggested for his club
    resp = await client.post(
        f"/dashboard/partidos/{match.id}/alineacion",
        data={
            auth.CSRF_FIELD: _csrf(page.text),
            "home-f": "4-3-3", "away-f": "4-4-2",
            "home-s0-n": "1", "home-s0-p": "Luis Arias",
            "home-s1-n": "3", "home-s1-p": "camilo restrepo",
            "away-s10-n": "9",
            "home-b0-n": "14", "home-b0-p": "Pedro Gil",
            "home-b1-p": "", "home-b1-n": "",
        },
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/dashboard/partidos/{match.id}/campo"

    rows = await MatchPlayer.filter(session_id=match.id).prefetch_related("prospect")
    assert len(rows) == 4
    by_name = {r.prospect.name: r for r in rows}
    assert by_name["Camilo Restrepo"].prospect_id == known.id  # reused, not duplicated
    assert (by_name["Camilo Restrepo"].slot, by_name["Camilo Restrepo"].shirt_number) == (1, 3)
    assert by_name["Pedro Gil"].slot is None  # bench
    temp = next(r for r in rows if r.prospect.is_temporary)
    assert (temp.side, temp.slot, temp.shirt_number) == ("away", 10, 9)
    assert (temp.prospect.team, temp.prospect.position) == ("Nacional", "Delantero centro")
    assert (await Prospect.get(name="Luis Arias")).position == "Portero"
    match = await Session.get(id=match.id)
    assert (match.home_formation, match.away_formation) == ("4-3-3", "4-4-2")
    assert await Prospect.filter(normalized_name=normalize_identity("Camilo Restrepo")).count() == 1


async def test_lineup_can_be_edited_and_a_repeated_player_keeps_one_place(client):
    match = await _web_match(client)
    url = f"/dashboard/partidos/{match.id}/alineacion"
    token = _csrf((await client.get(url)).text)
    await client.post(url, data={auth.CSRF_FIELD: token, "home-s0-p": "Luis Arias", "home-s5-p": "Luis Arias"})
    assert await MatchPlayer.all().count() == 1
    assert (await MatchPlayer.get()).slot == 0

    await client.post(url, data={auth.CSRF_FIELD: token, "home-s0-p": "Kevin Mier"})
    names = [r.prospect.name for r in await MatchPlayer.all().prefetch_related("prospect")]
    assert names == ["Kevin Mier"]
    page = await client.get(url)
    assert 'value="Kevin Mier"' in page.text


# ── Pitch ────────────────────────────────────────────────────────────────
async def test_pitch_shows_both_teams_and_the_bench_linking_to_sheets(client):
    match = await _web_match(client)
    url = f"/dashboard/partidos/{match.id}/alineacion"
    data = {auth.CSRF_FIELD: _csrf((await client.get(url)).text), "home-f": "4-3-3", "away-f": "4-3-3"}
    surnames = ["Arias", "Borja", "Cuesta", "Díaz", "Mier", "Ospina", "Pérez",
                "Quintero", "Rojas", "Sánchez", "Torres"]
    for side in ("home", "away"):
        for i, surname in enumerate(surnames):
            data[f"{side}-s{i}-n"] = str(i + 1)
            data[f"{side}-s{i}-p"] = f"{'Luis' if side == 'home' else 'Juan'} {surname}"
        data[f"{side}-b0-p"] = f"Suplente {'Local' if side == 'home' else 'Visitante'}"
    await client.post(url, data=data)

    page = await client.get(f"/dashboard/partidos/{match.id}/campo")
    assert page.status_code == 200
    assert page.text.count('class="token home') == 11
    assert page.text.count('class="token away') == 11
    assert "Suplente Local" in page.text and "Suplente Visitante" in page.text
    p = await Prospect.get(name="Luis Rojas")
    assert f"/dashboard/partidos/{match.id}/jugadores/{p.id}/evaluar" in page.text
    # And the match page lists every lineup player for scoring.
    detail = await client.get(f"/dashboard/partidos/{match.id}")
    assert detail.text.count("Evaluar</a>") == 24


async def test_pitch_without_lineup_shows_every_position_empty(client):
    match = await _web_match(client)
    page = await client.get(f"/dashboard/partidos/{match.id}/campo")
    assert page.text.count('class="token home unnamed empty"') == 11
    assert page.text.count('class="token away unnamed empty"') == 11
    assert page.text.count(f'action="/dashboard/partidos/{match.id}/puesto/') == 22
    assert '<span class="token-name" aria-hidden="true">DFC</span>' in page.text  # role, no number
    assert '<span class="shirt" aria-hidden="true"></span>' in page.text


async def test_evaluated_player_is_marked_on_the_pitch(client):
    from scouting_bot.evaluations import save_evaluation
    from scouting_bot.profiles import get_profile

    match = await _web_match(client)
    url = f"/dashboard/partidos/{match.id}/alineacion"
    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text),
                                 "home-s1-n": "3", "home-s1-p": "Camilo Restrepo"})
    p = await Prospect.get(name="Camilo Restrepo")
    await save_evaluation(match, p, profile=get_profile("lateral"), raw_scores={"1.1": 4})
    page = await client.get(f"/dashboard/partidos/{match.id}/campo")
    assert 'token home done' in page.text and 'token-rating" aria-hidden="true">4<' in page.text


def test_away_side_is_mirrored_on_the_top_half():
    left_back = FORMATIONS["4-3-3"][1]
    assert pitch_point(left_back, away=False) == (12, 84.5)
    assert pitch_point(left_back, away=True) == (88, 15.5)
    for slots in FORMATIONS.values():
        assert len(slots) == 11 and slots[0].role == "Portero"


def test_short_name():
    assert short_name("Camilo Restrepo") == "C. Restrepo"
    assert short_name("Camilo Restrepo Vélez") == "C. Restrepo"
    assert short_name("Jhojan Felipe Zúñiga Gómez") == "J. Zúñiga"
    assert short_name("Juan De La Rosa") == "J. De La Rosa"
    assert short_name("Ferrin") == "Ferrin"
    assert short_name("") == ""


# ── Finishing ────────────────────────────────────────────────────────────
async def test_finishing_a_web_match_ends_it(client):
    match = await _web_match(client)
    page = await client.get(f"/dashboard/partidos/{match.id}/campo")
    assert "Finalizar partido" in page.text
    await client.post(f"/dashboard/partidos/{match.id}/finalizar", data={"csrf": _csrf(page.text)})
    match = await Session.get(id=match.id)
    assert match.state == "ended" and match.ended_at is not None


async def test_a_bot_match_cannot_be_finished_from_the_panel(client, storage):
    bot = await storage.create_session(1, "Junior", "Nacional", None)
    page = await client.get(f"/dashboard/partidos/{bot.id}/campo")
    assert "Finalizar partido" not in page.text
    await client.post(f"/dashboard/partidos/{bot.id}/finalizar", data={"csrf": await _token(client)})
    assert (await Session.get(id=bot.id)).state == "active"


async def test_merge_moves_lineup_rows(storage):
    match = await storage.create_session(1, "Junior", "Nacional", None, origin=ORIGIN_WEB)
    keep = await storage.get_or_create_prospect(1, "Camilo Restrepo", "Junior")
    drop = await storage.get_or_create_prospect(1, "Camilo Restrepo", "Junior FC")
    other = await storage.create_session(1, "Junior", "Cali", None, origin=ORIGIN_WEB)
    await MatchPlayer.create(session=match, prospect=keep, side="home", slot=1)
    await MatchPlayer.create(session=match, prospect=drop, side="home", slot=2)
    await MatchPlayer.create(session=other, prospect=drop, side="home", slot=3)
    await storage.merge_prospects(keep.id, drop.id)
    rows = await MatchPlayer.filter(prospect_id=keep.id).order_by("session_id")
    assert [(r.session_id, r.slot) for r in rows] == [(match.id, 1), (other.id, 3)]


# ── Identifying players after the match (amendment A1) ───────────────────
async def _number_only_lineup(client, match: Session, number: str = "7") -> Prospect:
    url = f"/dashboard/partidos/{match.id}/alineacion"
    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text),
                                 "home-s1-n": number})
    return await Prospect.get(is_temporary=True)


def _sheet(match: Session, p: Prospect) -> str:
    return f"/dashboard/partidos/{match.id}/jugadores/{p.id}/evaluar"


async def test_an_unnamed_player_is_named_from_his_sheet_after_the_match(client):
    from scouting_bot.evaluations import save_evaluation
    from scouting_bot.profiles import get_profile

    match = await _web_match(client)
    temp = await _number_only_lineup(client, match)
    await save_evaluation(match, temp, profile=get_profile("lateral"), raw_scores={"1.1": 4})
    await Session.filter(id=match.id).update(state="ended")

    page = await client.get(_sheet(match, temp))
    assert "Identificar jugador" in page.text and "<details class=\"identify-box\" open" in page.text
    resp = await client.post(
        f"{_sheet(match, temp)[:-len('/evaluar')]}/identificar",
        data={"csrf": _csrf(page.text), "nombre": "Brayan Díaz", "dorsal": "7"},
    )
    assert resp.status_code == 303 and "identificado=1" in resp.headers["location"]
    named = await Prospect.get(id=temp.id)
    assert (named.name, named.is_temporary) == ("Brayan Díaz", False)
    assert (await Evaluation.get(prospect_id=temp.id)).rating == 4.0  # score kept
    pitch = await client.get(f"/dashboard/partidos/{match.id}/campo")
    assert "B. Díaz" in pitch.text and "Sin nombre" not in pitch.text


async def test_naming_after_an_existing_player_asks_before_merging(client):
    from scouting_bot.evaluations import save_evaluation
    from scouting_bot.profiles import get_profile

    match = await _web_match(client)
    known = await Prospect.create(
        agent_chat_id=match.agent_chat_id, name="Camilo Restrepo",
        normalized_name=normalize_identity("Camilo Restrepo"),
        team="Junior", normalized_team=normalize_name("Junior"),
    )
    temp = await _number_only_lineup(client, match, "3")
    await save_evaluation(match, temp, profile=get_profile("lateral"), raw_scores={"1.1": 5})
    url = f"{_sheet(match, temp)[:-len('/evaluar')]}/identificar"
    page = await client.get(_sheet(match, temp))

    resp = await client.post(url, data={"csrf": _csrf(page.text), "nombre": "camilo restrepo"})
    assert resp.status_code == 409 and "¿Es el mismo jugador?" in resp.text
    assert await Prospect.filter(id=temp.id).exists()  # nothing merged yet

    resp = await client.post(url, data={"csrf": _csrf(page.text), "nombre": "camilo restrepo",
                                        "unir": str(known.id)})
    assert resp.status_code == 303 and f"/jugadores/{known.id}/evaluar" in resp.headers["location"]
    assert not await Prospect.filter(id=temp.id).exists()
    assert (await Evaluation.get(prospect_id=known.id)).rating == 5.0
    row = await MatchPlayer.get(session_id=match.id)
    assert (row.prospect_id, row.shirt_number) == (known.id, 3)


async def test_a_named_players_dorsal_is_set_for_the_match(client):
    match = await _web_match(client)
    url = f"/dashboard/partidos/{match.id}/alineacion"
    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text),
                                 "home-s1-p": "Luis Arias"})
    p = await Prospect.get(name="Luis Arias")
    page = await client.get(_sheet(match, p))
    assert "Dorsal en este partido" in page.text and 'name="nombre"' not in page.text.split("identify-form")[1][:600]
    resp = await client.post(f"{_sheet(match, p)[:-len('/evaluar')]}/identificar",
                             data={"csrf": _csrf(page.text), "dorsal": "4"})
    assert resp.status_code == 303
    assert (await MatchPlayer.get(prospect_id=p.id)).shirt_number == 4
    assert (await Prospect.get(id=p.id)).shirt_number == 4  # he had none

    bad = await client.post(f"{_sheet(match, p)[:-len('/evaluar')]}/identificar",
                            data={"csrf": _csrf(page.text), "dorsal": "abc"})
    assert bad.status_code == 400 and "número de 1 a 99" in bad.text


async def test_the_lineup_editor_names_the_number_only_player_in_place(client):
    from scouting_bot.evaluations import save_evaluation
    from scouting_bot.profiles import get_profile

    match = await _web_match(client)
    temp = await _number_only_lineup(client, match, "9")
    await save_evaluation(match, temp, profile=get_profile("extremo"), raw_scores={"1.1": 3})
    url = f"/dashboard/partidos/{match.id}/alineacion"
    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text),
                                 "home-s1-n": "9", "home-s1-p": "Dylan Montoya"})
    row = await MatchPlayer.get(session_id=match.id)
    assert row.prospect_id == temp.id  # same player, now named
    named = await Prospect.get(id=temp.id)
    assert (named.name, named.is_temporary) == ("Dylan Montoya", False)
    assert await Evaluation.filter(prospect_id=temp.id).exists()
    assert await Prospect.all().count() == 1


# ── Formation step and optional lineup (amendment A2) ────────────────────
async def test_new_match_flow_goes_teams_formation_lineup_pitch(client):
    resp = await _new_match(client, local="Junior", visitante="Nacional")
    session = await Session.get()
    page = await client.get(resp.headers["location"])
    assert "¿Cómo forma cada equipo?" in page.text and page.text.count('type="radio"') == 8
    resp = await client.post(f"/dashboard/partidos/{session.id}/sistema",
                             data={auth.CSRF_FIELD: _csrf(page.text), "nuevo": "1",
                                   "home-f": "4-4-2", "away-f": "3-5-2"})
    assert resp.headers["location"] == f"/dashboard/partidos/{session.id}/alineacion?nuevo=1"
    session = await Session.get(id=session.id)
    assert (session.home_formation, session.away_formation) == ("4-4-2", "3-5-2")
    page = await client.get(resp.headers["location"])
    assert "Saltar, ver el campo" in page.text and f'href="/dashboard/partidos/{session.id}/campo"' in page.text
    assert 'name="home-f"' not in page.text  # the formation lives on its own step now


async def test_changing_the_formation_later_goes_back_to_the_pitch(client):
    match = await _web_match(client)
    page = await client.get(f"/dashboard/partidos/{match.id}/sistema")
    resp = await client.post(f"/dashboard/partidos/{match.id}/sistema",
                             data={auth.CSRF_FIELD: _csrf(page.text), "home-f": "bogus", "away-f": "4-2-3-1"})
    assert resp.headers["location"] == f"/dashboard/partidos/{match.id}/campo"
    match = await Session.get(id=match.id)
    assert (match.home_formation, match.away_formation) == ("4-3-3", "4-2-3-1")


async def test_tapping_an_empty_position_creates_one_player_for_it(client):
    match = await _web_match(client)
    token = _csrf((await client.get(f"/dashboard/partidos/{match.id}/campo")).text)
    url = f"/dashboard/partidos/{match.id}/puesto/away/4"
    resp = await client.post(url, data={"csrf": token})
    assert resp.status_code == 303
    row = await MatchPlayer.get(session_id=match.id).prefetch_related("prospect")
    assert (row.side, row.slot, row.shirt_number) == ("away", 4, None)
    assert row.prospect.is_temporary and row.prospect.position == "Lateral derecho"
    assert resp.headers["location"].endswith(f"/jugadores/{row.prospect_id}/evaluar")
    again = await client.post(url, data={"csrf": token})
    assert again.headers["location"] == resp.headers["location"]
    assert await Prospect.all().count() == 1 and await MatchPlayer.all().count() == 1
    assert (await client.post(f"/dashboard/partidos/{match.id}/puesto/away/11", data={"csrf": token})).status_code == 404
    assert (await client.post(f"/dashboard/partidos/{match.id}/puesto/x/1", data={"csrf": token})).status_code == 404


async def test_a_tapped_player_survives_a_blank_lineup_save_and_takes_a_dorsal(client):
    from scouting_bot.evaluations import save_evaluation
    from scouting_bot.profiles import get_profile

    match = await _web_match(client)
    token = _csrf((await client.get(f"/dashboard/partidos/{match.id}/campo")).text)
    await client.post(f"/dashboard/partidos/{match.id}/puesto/home/1", data={"csrf": token})
    tapped = await Prospect.get(is_temporary=True)
    await save_evaluation(match, tapped, profile=get_profile("lateral"), raw_scores={"1.1": 4})

    url = f"/dashboard/partidos/{match.id}/alineacion"
    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text)})  # all blank
    row = await MatchPlayer.get(session_id=match.id)
    assert (row.prospect_id, row.slot) == (tapped.id, 1)

    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text), "home-s1-n": "3"})
    row = await MatchPlayer.get(session_id=match.id)
    assert (row.prospect_id, row.shirt_number) == (tapped.id, 3)
    assert await Evaluation.filter(prospect_id=tapped.id).exists()
    assert await Prospect.all().count() == 1


async def test_a_named_player_without_dorsal_shows_a_blank_shirt(client):
    match = await _web_match(client)
    url = f"/dashboard/partidos/{match.id}/alineacion"
    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text), "home-s9-p": "Luis Arias"})
    page = await client.get(f"/dashboard/partidos/{match.id}/campo")
    p = await Prospect.get(name="Luis Arias")
    chunk = page.text.split(f"/jugadores/{p.id}/evaluar")[1][:400]
    assert '<span class="shirt" aria-hidden="true"></span>' in chunk and "L. Arias" in chunk
    assert ">?<" not in page.text


# ── Editing and deleting a match (build list #2) ─────────────────────────
async def test_editing_a_web_match_renames_teams_and_its_unnamed_players(client):
    match = await _web_match(client)
    temp = await _number_only_lineup(client, match, "7")
    known = await Prospect.create(agent_chat_id=match.agent_chat_id, name="Luis Arias",
                                  normalized_name="luis arias", team="Junior", normalized_team="junior")
    page = await client.get(f"/dashboard/partidos/{match.id}/editar")
    assert 'value="Junior"' in page.text and "Borrar partido" in page.text
    resp = await client.post(f"/dashboard/partidos/{match.id}/editar", data={
        auth.CSRF_FIELD: _csrf(page.text), "local": "Junior FC U17", "visitante": "Nacional",
        "fecha": "2026-09-28", "competicion": "Copa", "categoria": "", "sede": "Barranquilla"})
    assert resp.status_code == 303
    match = await Session.get(id=match.id)
    assert (match.home_team, match.home_team_category, match.competition, match.location) == (
        "Junior FC", "Sub-17", "Copa", "Barranquilla")
    assert match.match_date.astimezone(timezone(timedelta(hours=-5))).date().isoformat() == "2026-09-28"
    assert (await Prospect.get(id=temp.id)).team == "Junior FC"  # this match's unnamed player
    assert (await Prospect.get(id=known.id)).team == "Junior"      # a named player keeps his club


async def test_a_bot_match_can_be_edited_but_not_renamed_or_deleted(client, storage):
    bot = await storage.create_session(1, "Junior", "Nacional", None)
    page = await client.get(f"/dashboard/partidos/{bot.id}/editar")
    assert "readonly" in page.text and "Borrar partido" not in page.text
    await client.post(f"/dashboard/partidos/{bot.id}/editar", data={
        auth.CSRF_FIELD: _csrf(page.text), "local": "Otro", "visitante": "Nombre",
        "fecha": "2026-09-28", "competicion": "Liga"})
    bot = await Session.get(id=bot.id)
    assert (bot.home_team, bot.away_team, bot.competition) == ("Junior", "Nacional", "Liga")
    await client.post(f"/dashboard/partidos/{bot.id}/borrar", data={"csrf": _csrf(page.text)})
    assert await Session.filter(id=bot.id).exists()


async def test_deleting_a_web_match_cleans_up_and_restores_ratings(client, storage):
    from scouting_bot.evaluations import save_evaluation
    from scouting_bot.profiles import get_profile

    match = await _web_match(client)
    older = await storage.create_session(1, "Junior", "Cali", None,
                                         match_date=datetime(2026, 9, 1, 17, tzinfo=timezone.utc))
    temp = await _number_only_lineup(client, match, "7")
    named = await Prospect.create(agent_chat_id=match.agent_chat_id, name="Luis Arias",
                                  normalized_name="luis arias", team="Junior", normalized_team="junior")
    only_here = await Prospect.create(agent_chat_id=match.agent_chat_id, name="Pedro Gil",
                                      normalized_name="pedro gil", team="Junior", normalized_team="junior")
    await save_evaluation(older, named, profile=get_profile("lateral"), raw_scores={"1.1": 2})
    await save_evaluation(match, named, profile=get_profile("lateral"), raw_scores={"1.1": 5})
    await save_evaluation(match, only_here, profile=get_profile("lateral"), raw_scores={"1.1": 4})
    await save_evaluation(match, temp, profile=get_profile("lateral"), raw_scores={"1.1": 3})
    assert (await Prospect.get(id=named.id)).latest_rating == 5.0

    page = await client.get(f"/dashboard/partidos/{match.id}/borrar")
    assert "¿Borrar el partido?" in page.text and "3 evaluaciones" in page.text
    resp = await client.post(f"/dashboard/partidos/{match.id}/borrar", data={"csrf": _csrf(page.text)})
    assert resp.headers["location"] == "/dashboard/partidos?aviso=borrado"
    assert not await Session.filter(id=match.id).exists()
    assert not await Prospect.filter(id=temp.id).exists()           # unnamed, only this match
    named = await Prospect.get(id=named.id)
    assert (named.latest_rating, named.decision_status) == (2.0, "A seguir")  # falls back to Cali
    only_here = await Prospect.get(id=only_here.id)
    assert (only_here.latest_rating, only_here.decision_status) == (None, None)
    assert "Partido borrado" in (await client.get(resp.headers["location"])).text


# ── Substitutions (build list #3) ────────────────────────────────────────
async def _lineup_with_bench(client, match: Session) -> None:
    url = f"/dashboard/partidos/{match.id}/alineacion"
    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text),
                                 "home-s9-n": "9", "home-s9-p": "Luis Arias",
                                 "home-b0-n": "14", "home-b0-p": "Pedro Gil"})


async def test_a_bench_player_comes_on_for_a_starter(client):
    match = await _web_match(client)
    await _lineup_with_bench(client, match)
    starter = await MatchPlayer.get(session_id=match.id, slot=9)
    sub = await MatchPlayer.get(session_id=match.id, slot=None)
    page = await client.get(f"/dashboard/partidos/{match.id}/cambio")
    assert "Hacer un cambio" in page.text and "14 · Pedro Gil" in page.text
    resp = await client.post(f"/dashboard/partidos/{match.id}/cambio", data={
        auth.CSRF_FIELD: _csrf(page.text), "lado": "home",
        "sale-home": str(starter.id), "entra-home": str(sub.id), "minuto": "62"})
    assert resp.status_code == 303
    starter, sub = await MatchPlayer.get(id=starter.id), await MatchPlayer.get(id=sub.id)
    assert (starter.slot, starter.subbed_off, starter.sub_minute) == (None, True, 62)
    assert (sub.slot, sub.came_on_for_id, sub.sub_minute) == (9, starter.prospect_id, 62)
    obs = await Observation.get(session_id=match.id, is_substitution=True)
    assert (obs.prospect_id, obs.minute, obs.raw_quote) == (sub.prospect_id, 62, "Entra 14 · Pedro Gil por 9 · Luis Arias")

    pitch = await client.get(f"/dashboard/partidos/{match.id}/campo")
    assert 'class="token-sub"' in pitch.text and "Deshacer cambio" in pitch.text
    assert "↓ 62" in pitch.text

    # The lineup editor keeps the history when it is re-saved.
    url = f"/dashboard/partidos/{match.id}/alineacion"
    await client.post(url, data={auth.CSRF_FIELD: _csrf((await client.get(url)).text),
                                 "home-s9-n": "14", "home-s9-p": "Pedro Gil",
                                 "home-b0-n": "9", "home-b0-p": "Luis Arias"})
    assert (await MatchPlayer.get(prospect_id=starter.prospect_id)).subbed_off is True


async def test_someone_typed_now_can_come_on_and_the_change_can_be_undone(client):
    match = await _web_match(client)
    await _lineup_with_bench(client, match)
    starter = await MatchPlayer.get(session_id=match.id, slot=9)
    page = await client.get(f"/dashboard/partidos/{match.id}/cambio")
    await client.post(f"/dashboard/partidos/{match.id}/cambio", data={
        auth.CSRF_FIELD: _csrf(page.text), "lado": "home",
        "sale-home": str(starter.id), "entra-home": "nuevo", "dorsal": "20"})
    newcomer = await MatchPlayer.get(session_id=match.id, slot=9)
    assert newcomer.shirt_number == 20 and newcomer.id != starter.id

    pitch = await client.get(f"/dashboard/partidos/{match.id}/campo")
    await client.post(f"/dashboard/partidos/{match.id}/cambio/{newcomer.id}/deshacer", data={"csrf": _csrf(pitch.text)})
    starter, newcomer = await MatchPlayer.get(id=starter.id), await MatchPlayer.get(id=newcomer.id)
    assert (starter.slot, starter.subbed_off) == (9, False)
    assert (newcomer.slot, newcomer.came_on_for_id) == (None, None)
    assert not await Observation.filter(session_id=match.id, is_substitution=True).exists()


async def test_a_change_needs_who_goes_off_and_who_comes_on(client):
    match = await _web_match(client)
    await _lineup_with_bench(client, match)
    page = await client.get(f"/dashboard/partidos/{match.id}/cambio")
    resp = await client.post(f"/dashboard/partidos/{match.id}/cambio", data={
        auth.CSRF_FIELD: _csrf(page.text), "lado": "home", "sale-home": "", "entra-home": "nuevo", "minuto": "abc"})
    assert resp.status_code == 400
    for msg in ("Elige quién sale", "número entre 0 y 150"):
        assert msg in resp.text


# ── Several matches open at once (prod hotfix) ───────────────────────────
async def test_web_matches_can_be_open_alongside_each_other_and_a_bot_match(storage):
    """Production had a hand-made unique index allowing one active session per
    scout, which made a second panel match fail with a 500. Migration 15
    narrows it to bot matches; apply its real SQL here (tests build the schema
    from the models, which never had the index) and check both rules."""
    import importlib.util
    from pathlib import Path

    from tortoise import Tortoise
    from tortoise.exceptions import IntegrityError

    conn = Tortoise.get_connection("default")
    legacy = ('CREATE UNIQUE INDEX IF NOT EXISTS "uq_sessions_one_active_per_agent" '
              "ON \"sessions\" (\"agent_chat_id\") WHERE \"state\" = 'active'")
    path = Path(__file__).resolve().parent.parent / "migrations/models/15_20261002000000_one_active_bot_match.py"
    spec = importlib.util.spec_from_file_location("one_active_bot_match", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    await conn.execute_script(legacy)
    try:
        await conn.execute_script(await migration.upgrade(conn))
        await storage.create_session(7, "Junior", "Nacional", None)  # the bot's open match
        await storage.create_session(7, "América", "Cali", None, origin=ORIGIN_WEB)
        await storage.create_session(7, "Millonarios", "Once Caldas", None, origin=ORIGIN_WEB)
        assert await Session.filter(agent_chat_id=7, state="active").count() == 3
        with pytest.raises(IntegrityError):  # still only one open bot match
            await storage.create_session(7, "Santa Fe", "Tolima", None)
    finally:
        await conn.execute_script('DROP INDEX IF EXISTS "uq_sessions_one_active_bot_per_agent";'
                                  'DROP INDEX IF EXISTS "uq_sessions_one_active_per_agent";')
