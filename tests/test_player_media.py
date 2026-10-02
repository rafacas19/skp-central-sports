"""Phase 4: photos uploaded on the panel, the video link, and adding a profile."""

from datetime import datetime, timezone

import httpx
import pytest_asyncio

from scouting_bot import profiles
from scouting_bot.config import settings
from scouting_bot.dashboard import auth
from scouting_bot.models import Prospect, ProspectPhoto, Session
from scouting_bot.profiles import Criterion, Profile, Section
from scouting_bot.taxonomy import normalize_identity, normalize_name

PASSWORD = "prueba-scouting"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64


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


async def _player(**kw) -> Prospect:
    return await Prospect.create(
        agent_chat_id=1, name="Camilo Restrepo", normalized_name=normalize_identity("Camilo Restrepo"),
        team="Junior", normalized_team=normalize_name("Junior"), **kw,
    )


async def _upload(client, p: Prospect, content: bytes, name: str = "foto.jpg"):
    token = _csrf((await client.get(f"/dashboard/jugadores/{p.id}/editar")).text)
    return await client.post(
        f"/dashboard/jugadores/{p.id}/foto",
        data={"csrf": token}, files={"foto": (name, content, "image/jpeg")},
    )


# ── Photos ───────────────────────────────────────────────────────────────
async def test_uploaded_photo_is_stored_and_served(client):
    p = await _player()
    resp = await _upload(client, p, PNG, "foto.png")
    assert resp.status_code == 303 and resp.headers["location"].endswith("?foto=ok")
    stored = await ProspectPhoto.get(prospect_id=p.id)
    assert stored.mime == "image/png"  # from the bytes, not the declared type
    served = await client.get(f"/dashboard/foto/{p.id}")
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"

    await _upload(client, p, WEBP, "foto.webp")  # replacing keeps one row
    assert await ProspectPhoto.all().count() == 1
    assert (await client.get(f"/dashboard/foto/{p.id}")).content == WEBP


async def test_upload_rejects_wrong_types_and_big_files(client):
    p = await _player()
    resp = await _upload(client, p, b"GIF89a" + b"\x00" * 20, "foto.gif")
    assert resp.headers["location"].endswith("?foto=formato")
    resp = await _upload(client, p, JPEG + b"\x00" * (5 * 1024 * 1024))
    assert resp.headers["location"].endswith("?foto=grande")
    resp = await _upload(client, p, b"")
    assert resp.headers["location"].endswith("?foto=vacia")
    assert not await ProspectPhoto.exists()
    page = await client.get(f"/dashboard/jugadores/{p.id}/editar?foto=formato")
    assert "Solo se aceptan fotos JPG, PNG o WEBP" in page.text


async def test_uploaded_photo_shows_on_cards_and_wins_over_telegram(client):
    p = await _player(photo_file_id="telegram-file", latest_rating=4.0, decision_status="Muy interesante")
    await _upload(client, p, JPEG)
    page = await client.get("/dashboard/decisiones")
    assert f"/dashboard/foto/{p.id}" in page.text
    served = await client.get(f"/dashboard/foto/{p.id}")  # never reaches Telegram
    assert served.status_code == 200 and served.content == JPEG

    other = await Prospect.create(agent_chat_id=1, name="Luis Arias", normalized_name="luis arias",
                                  latest_rating=3.0, decision_status="Interesante")
    assert f"/dashboard/foto/{other.id}" not in (await client.get("/dashboard/decisiones")).text


async def test_removing_the_uploaded_photo(client):
    p = await _player()
    await _upload(client, p, JPEG)
    page = await client.get(f"/dashboard/jugadores/{p.id}/editar")
    resp = await client.post(f"/dashboard/jugadores/{p.id}/foto/quitar", data={"csrf": _csrf(page.text)})
    assert resp.headers["location"].endswith("?foto=quitada")
    assert not await ProspectPhoto.exists()
    assert (await client.get(f"/dashboard/foto/{p.id}")).status_code == 404


async def test_upload_requires_csrf(client):
    p = await _player()
    resp = await client.post(
        f"/dashboard/jugadores/{p.id}/foto", data={"csrf": "0" * 20},
        files={"foto": ("f.jpg", JPEG, "image/jpeg")},
    )
    assert resp.status_code == 400 and not await ProspectPhoto.exists()


async def test_merge_moves_an_uploaded_photo_only_when_the_survivor_has_none(storage):
    keep, drop = await _player(), await Prospect.create(agent_chat_id=1, name="Camilo Restrepo", normalized_name="camilo restrepo", team="Junior FC", normalized_team="junior fc")
    await ProspectPhoto.create(prospect_id=drop.id, data=PNG, mime="image/png")
    await storage.merge_prospects(keep.id, drop.id)
    assert (await ProspectPhoto.get(prospect_id=keep.id)).data == PNG


# ── Video link ───────────────────────────────────────────────────────────
async def _edit(client, p: Prospect, **fields) -> httpx.Response:
    page = await client.get(f"/dashboard/jugadores/{p.id}/editar")
    data = {auth.CSRF_FIELD: _csrf(page.text), "nombre": p.name, "equipo": p.team or ""}
    data.update(fields)
    return await client.post(f"/dashboard/jugadores/{p.id}/editar", data=data)


async def test_video_link_is_saved_and_shown(client):
    p = await _player()
    resp = await _edit(client, p, video="https://youtu.be/abc123")
    assert resp.status_code == 303
    assert (await Prospect.get(id=p.id)).video_url == "https://youtu.be/abc123"
    assert 'href="https://youtu.be/abc123"' in (await client.get(f"/dashboard/jugadores/{p.id}")).text


async def test_video_link_must_be_a_web_address(client):
    p = await _player()
    resp = await _edit(client, p, video="javascript:alert(1)")
    assert resp.status_code == 400 and "http:// o https://" in resp.text
    assert (await Prospect.get(id=p.id)).video_url is None
    await _edit(client, p, video="https://youtu.be/x")
    await _edit(client, p, video="")  # clearing it
    assert (await Prospect.get(id=p.id)).video_url is None


async def test_video_link_appears_on_the_report(client):
    from scouting_bot.evaluations import save_evaluation

    p = await _player(position="Lateral izquierdo", video_url="https://drive.google.com/v")
    match = await Session.create(agent_chat_id=1, home_team="Junior", away_team="Nacional",
                                 match_date=datetime(2026, 9, 20, tzinfo=timezone.utc))
    await save_evaluation(match, p, profile=profiles.get_profile("lateral"), raw_scores={"1.1": 4})
    page = await client.get(f"/dashboard/jugadores/{p.id}/informe")
    assert 'href="https://drive.google.com/v">VIDEO' in page.text


# ── A new profile is data, not code ──────────────────────────────────────
async def test_adding_a_goalkeeper_profile_needs_only_the_profile_and_a_mapping(client, monkeypatch):
    portero = Profile(
        "portero", "Portero",
        (
            Section(1, "1. TÉCNICA", (Criterion("1.1", "Blocaje", "Atrapar el balón con seguridad."),)),
            Section(2, "2. TÁCTICA DEFENSIVA", (Criterion("2.1", "Salidas", "Dominar el área."),)),
            Section(3, "3. TÁCTICA OFENSIVA", (Criterion("3.1", "Juego con el pie", "Iniciar la jugada."),)),
            Section(4, "4. CONDICIONAL", (Criterion("4.1", "Reflejos", "Reaccionar a tiempo."),)),
            Section(5, "5. MENTAL (COGNITIVO Y VOLITIVO)", (Criterion("5.1", "Concentración", "Atención continua."),)),
        ),
        build="Atlético", height="Alto",
    )
    # The two edits the client's sheet would need: the profile, and one mapping line.
    monkeypatch.setattr(profiles, "PROFILES", (*profiles.PROFILES, portero))
    monkeypatch.setattr(profiles, "_BY_KEY", {**profiles._BY_KEY, "portero": portero})
    monkeypatch.setitem(profiles.ROLE_PROFILES, "Portero", "portero")

    keeper = await _player(position="Portero")
    match = await Session.create(agent_chat_id=1, home_team="Junior", away_team="Nacional")
    url = f"/dashboard/partidos/{match.id}/jugadores/{keeper.id}/evaluar"
    page = await client.get(url)
    assert "Blocaje" in page.text and "Valoración del partido" not in page.text
    await client.post(url, data={auth.CSRF_FIELD: _csrf(page.text), "perfil": "portero", "s-1.1": "5", "s-4.1": "4"})
    keeper = await Prospect.get(id=keeper.id)
    assert keeper.latest_rating == 4.5
    report = await client.get(f"/dashboard/jugadores/{keeper.id}/informe")
    assert "PERFIL: PORTERO" in report.text and "Blocaje" in report.text


def test_goalkeepers_are_scored_on_the_clients_arquero_profile():
    assert profiles.default_profile("Portero").name == "Arquero"
    assert profiles.PROFILES[0].key == "arquero"  # pitch order: goalkeeper first
