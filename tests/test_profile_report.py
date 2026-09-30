"""The player profile report: numbers across matches, the editable AI text, PDF."""

from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

from scouting_bot.ai.mock import MockAIProvider
from scouting_bot.config import settings
from scouting_bot.dashboard import auth, profile_report
from scouting_bot.evaluations import save_evaluation
from scouting_bot.models import Observation, ProfileReport, Prospect, Session
from scouting_bot.profiles import get_profile
from scouting_bot.taxonomy import normalize_identity, normalize_name

from .test_evaluations import CLIENT_EXAMPLE

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


async def _match(day: int, away: str = "Nacional") -> Session:
    return await Session.create(
        agent_chat_id=1, home_team="Junior", away_team=away, state="ended",
        match_date=datetime(2026, 9, day, 18, tzinfo=timezone.utc),
    )


async def _player(position: str = "Lateral izquierdo") -> Prospect:
    return await Prospect.create(
        agent_chat_id=1, name="Camilo Restrepo", normalized_name=normalize_identity("Camilo Restrepo"),
        team="Junior", normalized_team=normalize_name("Junior"), position=position,
    )


async def _scored(player: Prospect, *matches: tuple[Session, dict], profile: str = "lateral"):
    for match, scores in matches:
        await save_evaluation(match, player, profile=get_profile(profile), raw_scores=scores,
                              build_ok=True, height_ok=False)


def _url(p: Prospect, suffix: str = "") -> str:
    return f"/dashboard/jugadores/{p.id}/informe{suffix}"


# ── The numbers ──────────────────────────────────────────────────────────
async def test_report_shows_the_clients_example_in_their_layout(client):
    player = await _player()
    await _scored(player, (await _match(20), CLIENT_EXAMPLE))
    page = await client.get(_url(player))
    assert page.status_code == 200
    html = page.text
    for piece in ("DETECCIÓN DE TALENTO", "PERFIL: LATERAL IZQUIERDO", "CAMILO RESTREPO",
                  "PARTIDO 1", "Puntuación 1 a 5", "1. TÉCNICA", "Posicionamiento Defensivo",
                  "1. Técnica — 3,5/5", "2. Táctica defensiva — 3,7/5",
                  "3. Táctica ofensiva — 3,2/5", "4. Condicional — 3,3/5",
                  "5. Mental (cognitivo y volitivo) — 3,5/5", "6. CONTEXTURA", "Atlético",
                  "7. ESTATURA", "Mediano", "OBSERVACIONES", "Generar texto con IA"):
        assert piece in html, piece
    assert "PARTIDO 2" not in html


async def test_two_matches_are_two_columns_and_averages_cover_both(storage):
    player = await _player()
    old, new = await _match(10, "Cali"), await _match(20)
    await _scored(player, (new, {"1.1": 5, "1.2": 4}), (old, {"1.1": 2, "1.3": 3}))
    ctx = await profile_report.report_context(player, None)
    sheet = ctx["sheet"]
    assert [m["label"] for m in sheet["matches"]] == ["PARTIDO 1", "PARTIDO 2"]
    assert [m["opponent"] for m in sheet["matches"]] == ["Cali", "Nacional"]  # oldest first
    tecnica = sheet["sections"][0]
    assert tecnica["criteria"][0]["scores"] == [2, 5]
    assert tecnica["criteria"][2]["scores"] == [3, None]
    assert tecnica["average"] == "3,5"  # (2 + 3 + 5 + 4) / 4 over both columns
    assert sheet["sections"][1]["average"] == "—"
    assert sheet["build"]["values"] == ["Sí", "Sí"] and sheet["height"]["values"] == ["No", "No"]


async def test_each_profile_reports_only_its_own_matches(client):
    player = await _player()
    m1, m2 = await _match(10), await _match(20)
    await _scored(player, (m1, {"1.1": 4}), profile="lateral")
    await _scored(player, (m2, {"1.1": 2}), profile="extremo")
    page = await client.get(_url(player))
    assert "Controles" in page.text and 'name="perfil"' in page.text  # latest = Extremo
    assert page.text.count("PARTIDO 1") == 1 and "PARTIDO 2" not in page.text
    page = await client.get(_url(player) + "?perfil=lateral")
    assert "Posicionamiento Defensivo" in page.text and "PARTIDO 2" not in page.text


async def test_a_player_without_profile_sheets_gets_an_explanation(client):
    player = await _player(position="Portero")
    await save_evaluation(await _match(20), player, profile=None, raw_scores={}, single_rating=4.0)
    page = await client.get(_url(player))
    assert page.status_code == 200 and "Todavía no hay evaluaciones por perfil" in page.text


async def test_player_page_links_to_the_report(client):
    player = await _player()
    assert _url(player) in (await client.get(f"/dashboard/jugadores/{player.id}")).text


# ── The AI text ──────────────────────────────────────────────────────────
async def _generate(client, player: Prospect, profile: str = "lateral") -> httpx.Response:
    page = await client.get(_url(player))
    return await client.post(_url(player, "/generar"), data={"perfil": profile, "csrf": _csrf(page.text)})


async def test_generating_stores_an_editable_draft(client):
    player = await _player()
    await _scored(player, (await _match(20), CLIENT_EXAMPLE))
    resp = await _generate(client, player)
    assert resp.status_code == 303
    report = await ProfileReport.get(prospect_id=player.id, profile="lateral")
    assert report.summary and set(report.sections) == {"1", "2", "3", "4", "5"}
    assert not report.edited
    page = await client.get(_url(player))
    assert report.sections["1"] in page.text
    assert "Conclusión:" in page.text

    page = await client.get(_url(player, "/texto") + "?perfil=lateral")
    form = {auth.CSRF_FIELD: _csrf(page.text), "perfil": "lateral", "resumen": "Lateral muy sólido."}
    form.update({f"seccion-{n}": f"Texto propio {n}. Conclusión: bien." for n in range(1, 6)})
    await client.post(_url(player, "/texto"), data=form)
    report = await ProfileReport.get(prospect_id=player.id, profile="lateral")
    assert report.edited and report.summary == "Lateral muy sólido."
    assert "Texto propio 3" in (await client.get(_url(player))).text


async def test_new_scores_never_overwrite_an_edited_text(client):
    player = await _player()
    match = await _match(20)
    await _scored(player, (match, {"1.1": 4}))
    await _generate(client, player)
    await ProfileReport.filter(prospect_id=player.id).update(summary="Mío", edited=True)

    await _scored(player, (await _match(25), {"1.1": 2}))
    page = await client.get(_url(player))
    assert "Hay evaluaciones nuevas" in page.text and "Regenerar con IA" in page.text
    report = await ProfileReport.get(prospect_id=player.id)
    assert report.summary == "Mío" and report.edited

    await _generate(client, player)  # explicit regenerate replaces it
    report = await ProfileReport.get(prospect_id=player.id)
    assert report.summary != "Mío" and not report.edited


async def test_unedited_text_is_redrafted_in_the_background(client, monkeypatch):
    player = await _player()
    await _scored(player, (await _match(20), {"1.1": 4}))
    await _generate(client, player)
    first = (await ProfileReport.get(prospect_id=player.id)).watermark

    await _scored(player, (await _match(25), {"1.1": 2}))
    page = await client.get(_url(player))  # background task runs after the response
    assert "se está actualizando" in page.text
    report = await ProfileReport.get(prospect_id=player.id)
    assert report.watermark != first and "2 partido(s)" in report.summary


async def test_an_ai_failure_still_renders_the_report(client, monkeypatch):
    async def boom(self, report):
        raise RuntimeError("API down")

    monkeypatch.setattr(MockAIProvider, "draft_profile_report", boom)
    player = await _player()
    await _scored(player, (await _match(20), {"1.1": 4}))
    resp = await _generate(client, player)
    assert "aviso=ia_error" in resp.headers["location"]
    page = await client.get(resp.headers["location"])
    assert page.status_code == 200 and "No se pudo generar el texto" in page.text
    assert not await ProfileReport.exists()


async def test_the_ai_gets_the_scores_and_the_notes(storage, monkeypatch):
    seen = {}

    async def spy(self, report):
        seen.update(report)
        return {"summary": "ok", "sections": {}}

    monkeypatch.setattr(MockAIProvider, "draft_profile_report", spy)
    monkeypatch.setattr(profile_report, "get_provider", MockAIProvider)
    player = await _player()
    match = await _match(20)
    await Observation.create(session=match, prospect=player, raw_quote="Sube mucho por banda")
    await _scored(player, (match, CLIENT_EXAMPLE))
    await profile_report.draft(player.id, "lateral")
    assert seen["role"] == "Lateral izquierdo" and seen["profile"] == "Lateral"
    assert seen["sections"][0]["average"] == "3,5"
    assert seen["sections"][0]["criteria"][0] == {"code": "1.1", "name": "Control", "scores": [4]}
    assert seen["notes"] == ["Sube mucho por banda"]


# ── PDF ──────────────────────────────────────────────────────────────────
async def test_pdf_is_one_landscape_letter_page(client):
    pytest.importorskip("weasyprint")
    player = await _player()
    second = {code: max(1, v - 1) for code, v in CLIENT_EXAMPLE.items()}
    await _scored(player, (await _match(10), CLIENT_EXAMPLE), (await _match(20), second))
    await _generate(client, player)

    resp = await client.get(_url(player, ".pdf"))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")

    # Laid out, it is one page of US Letter landscape (11 x 8.5 in = 1056 x 816 px).
    ctx = await profile_report.report_context(player, None)
    doc = profile_report.render_document(profile_report.print_html(ctx))
    assert len(doc.pages) == 1
    assert (round(doc.pages[0].width), round(doc.pages[0].height)) == (1056, 816)


async def test_pdf_with_five_matches_still_fits_one_page(storage, monkeypatch):
    pytest.importorskip("weasyprint")
    monkeypatch.setattr(profile_report, "get_provider", MockAIProvider)
    player = await _player()
    for day in (5, 10, 15, 20, 25):
        await _scored(player, (await _match(day), CLIENT_EXAMPLE))
    await profile_report.draft(player.id, "lateral")
    ctx = await profile_report.report_context(player, None)
    doc = profile_report.render_document(profile_report.print_html(ctx))
    assert len(doc.pages) == 1
