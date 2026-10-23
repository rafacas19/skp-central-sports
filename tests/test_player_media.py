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


def test_the_clients_sheet_has_no_goalkeeper_profile_yet():
    assert "portero" not in {p.key for p in profiles.PROFILES}
    assert profiles.default_profile("Portero") is None


# ── Feedback 26/10: photo on the profile, Transfermarkt, metres, contract month ──
async def test_the_profile_header_shows_the_photo_or_initials(client):
    p = await _player()
    page = await client.get(f"/dashboard/jugadores/{p.id}")
    assert "profile-avatar" in page.text and "<span>CR</span>" in page.text
    assert f'src="/dashboard/foto/{p.id}"' not in page.text
    await _upload(client, p, JPEG)
    page = await client.get(f"/dashboard/jugadores/{p.id}")
    assert f'<img src="/dashboard/foto/{p.id}" alt="Foto de Camilo Restrepo">' in page.text
    assert f'href="/dashboard/jugadores/{p.id}/editar#foto"' in page.text


async def test_transfermarkt_link_is_saved_shown_and_on_the_report(client):
    from scouting_bot.evaluations import save_evaluation

    url = "https://www.transfermarkt.co/julian-beltran/profil/spieler/1415087"
    p = await _player(position="Lateral izquierdo")
    resp = await _edit(client, p, transfermarkt=url)
    assert resp.status_code == 303
    assert (await Prospect.get(id=p.id)).transfermarkt_url == url
    page = await client.get(f"/dashboard/jugadores/{p.id}")
    assert f'href="{url}" target="_blank"' in page.text and "Transfermarkt" in page.text
    assert f'value="{url}"' in (await client.get(f"/dashboard/jugadores/{p.id}/editar")).text
    match = await Session.create(agent_chat_id=1, home_team="Junior", away_team="Nacional",
                                 match_date=datetime(2026, 9, 20, tzinfo=timezone.utc))
    await save_evaluation(match, p, profile=profiles.get_profile("lateral"), raw_scores={"1.1": 4})
    report = await client.get(f"/dashboard/jugadores/{p.id}/informe")
    assert f'href="{url}">TRANSFERMARKT' in report.text


async def test_transfermarkt_link_must_be_a_transfermarkt_page(client):
    p = await _player()
    resp = await _edit(client, p, transfermarkt="https://youtu.be/abc")
    assert resp.status_code == 400 and "transfermarkt" in resp.text
    assert (await Prospect.get(id=p.id)).transfermarkt_url is None
    for ok in ("https://www.transfermarkt.es/x/profil/spieler/1", "https://transfermarkt.com/x"):
        assert (await _edit(client, p, transfermarkt=ok)).status_code == 303


async def test_height_is_typed_and_shown_in_metres(client):
    p = await _player()
    for typed in ("1,75", "1.75", "175", "1,75 m"):
        assert (await _edit(client, p, estatura=typed)).status_code == 303, typed
        assert (await Prospect.get(id=p.id)).height_cm == 175
    page = await client.get(f"/dashboard/jugadores/{p.id}")
    assert "1,75 m" in page.text and "175 cm" not in page.text
    form = await client.get(f"/dashboard/jugadores/{p.id}/editar")
    assert 'name="estatura" value="1,75"' in form.text
    resp = await _edit(client, p, estatura="17,5")
    assert resp.status_code == 400 and "1,75" in resp.text
    assert (await Prospect.get(id=p.id)).height_cm == 175


async def test_contract_takes_a_month_and_a_year(client):
    p = await _player()
    assert (await _edit(client, p, contrato_mes="6", contrato_hasta="2027")).status_code == 303
    stored = await Prospect.get(id=p.id)
    assert (stored.contract_month, stored.contract_year) == (6, 2027)
    assert "06/2027" in (await client.get(f"/dashboard/jugadores/{p.id}")).text
    form = await client.get(f"/dashboard/jugadores/{p.id}/editar")
    assert '<option value="6" selected>Junio</option>' in form.text

    assert (await _edit(client, p, contrato_hasta="2028")).status_code == 303  # year only
    stored = await Prospect.get(id=p.id)
    assert (stored.contract_month, stored.contract_year) == (None, 2028)
    page = (await client.get(f"/dashboard/jugadores/{p.id}")).text
    assert "Contrato hasta</span>2028" in page

    resp = await _edit(client, p, contrato_mes="6")
    assert resp.status_code == 400 and "Indica también el año" in resp.text


def test_heights_parse_and_format():
    from scouting_bot.measures import format_height, height_input, height_m, parse_height

    assert parse_height("1,87") == (187, None) and parse_height("187cm") == (187, None)
    assert parse_height("") == (None, None)
    assert parse_height("2,6")[0] is None and parse_height("abc")[0] is None
    assert (format_height(175), height_input(180), height_m(175)) == ("1,75 m", "1,80", 1.75)
    assert format_height(None) == "" and height_m(None) is None
