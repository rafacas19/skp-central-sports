"""End-to-end tests: real Telegram Update JSON → FastAPI webhook → PTB → handlers
→ ScoutingService → Postgres, with only the Telegram network faked.

Observation-first: no lineup gate. After /nuevo, observations are captured
immediately; the deterministic MockAIProvider extracts identity from the text."""

import csv
import io

import pytest

from .e2e_harness import message_update


def _team_button(outbox, team_hint: str) -> str | None:
    """The `team:<i>` callback_data whose button label mentions team_hint."""
    for kb in outbox.keyboards():
        for row in kb.inline_keyboard:
            for btn in row:
                if (
                    btn.callback_data
                    and btn.callback_data.startswith("team:")
                    and team_hint in btn.text
                ):
                    return btn.callback_data
    return None


@pytest.mark.asyncio
async def test_e2e_happy_path_observation_then_finish_csv(harness):
    # 1. Start a match — observations can start immediately (no lineup step).
    await harness.send_text("/nuevo Millonarios vs América")
    assert "Partido iniciado" in (harness.outbox.last_text() or "")

    # 2. A named observation is captured straight away.
    harness.outbox.clear()
    await harness.send_text("Castro, volante, América, se asocia bien por dentro")
    assert any(t.startswith("✅") for t in harness.outbox.texts())

    # 3. Finish → CSV with the observation.
    harness.outbox.clear()
    await harness.send_text("/finalizar")
    docs = harness.outbox.documents_sent()
    assert len(docs) == 1
    filename, content = docs[0]
    assert filename == "informe_Millonarios_vs_América.csv"
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    body = "\n".join(",".join(r) for r in rows)
    assert "Castro" in body and "se asocia bien por dentro" in body


@pytest.mark.asyncio
async def test_e2e_number_only_asks_for_team(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    harness.outbox.clear()

    # A number with no team → the bot must ask which team (don't guess).
    await harness.send_text("#7 muy rápido en el 1vs1")
    assert harness.outbox.texts_containing("¿Te refieres al")
    teams = [d for d in harness.outbox.callback_data() if d.startswith("team:")]
    assert len(teams) >= 2

    # Pick América → stored, queue drained.
    pick = _team_button(harness.outbox, "América")
    assert pick is not None
    prompts_before = harness.outbox.count_text("¿Te refieres al")
    await harness.tap_button(pick)
    assert harness.outbox.texts_containing("Registrada para América")
    assert harness.outbox.count_text("¿Te refieres al") == prompts_before


@pytest.mark.asyncio
async def test_e2e_number_with_team_is_captured_directly(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    harness.outbox.clear()
    await harness.send_text("#10 de Millonarios tiene buen pase filtrado")
    # Team stated → no ambiguity, just an ack.
    assert any(t.startswith("✅") for t in harness.outbox.texts())
    assert not harness.outbox.texts_containing("¿Te refieres al")


@pytest.mark.asyncio
async def test_e2e_team_note(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    harness.outbox.clear()
    await harness.send_text("América juega 4-2-3-1 y presionan muy alto")
    assert harness.outbox.texts()  # acked
    assert not harness.outbox.texts_containing("¿Te refieres al")


@pytest.mark.asyncio
async def test_e2e_voice_note_is_transcribed_and_classified(harness):
    """Voice → transcription → identity extraction → capture. The mock transcribes
    to a fixed '#8 …' line with no team → the bot asks which team."""
    await harness.send_text("/nuevo Millonarios vs América")
    harness.outbox.clear()
    await harness.send_voice()
    assert harness.outbox.texts_containing("¿Te refieres al")
    pick = _team_button(harness.outbox, "Millonarios")
    await harness.tap_button(pick)
    assert harness.outbox.texts_containing("Registrada")


# ── new commands (Phase 3) ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_e2e_scout_name_and_team_note_and_rating(harness):
    await harness.send_text("/yo Rafa")
    assert harness.outbox.texts_containing("Rafa")

    await harness.send_text("/nuevo Millonarios vs América")
    harness.outbox.clear()

    await harness.send_text("/equipo América presiona muy alto")
    assert harness.outbox.texts_containing("Nota de equipo")

    await harness.send_text("Castro de América buen pase")
    await harness.send_text("/valorar Castro 7.5")
    assert harness.outbox.texts_containing("valoración 7.5")


@pytest.mark.asyncio
async def test_e2e_foto_unknown_player_flow(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    harness.outbox.clear()

    await harness.send_text("/foto")
    assert harness.outbox.texts_containing("Envía la foto")

    await harness.send_photo()
    assert harness.outbox.texts_containing("Foto guardada")

    await harness.send_text("muy alto, gana todo por arriba")
    assert harness.outbox.texts_containing("foto")  # attached to the temp profile


@pytest.mark.asyncio
async def test_e2e_one_active_match_offers_to_close(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    harness.outbox.clear()
    await harness.send_text("/nuevo Nacional vs Cali")
    # Doesn't silently start a second — asks to close the current one.
    assert harness.outbox.texts_containing("Ya tienes un partido activo")
    assert "newmatch:close" in harness.outbox.callback_data()

    await harness.tap_button("newmatch:close")
    assert harness.outbox.texts_containing("Partido anterior cerrado")
    assert harness.outbox.texts_containing("Nacional vs Cali")


# ── cross-match player report (Phase 4) ───────────────────────────────────────
@pytest.mark.asyncio
async def test_e2e_player_report_across_matches_with_decision(harness):
    # Match 1.
    await harness.send_text("/nuevo América vs Nacional")
    await harness.send_text("Castro de América se asocia bien")
    await harness.send_text("/finalizar")
    # Match 2 (same scout/chat) — Castro again.
    await harness.send_text("/nuevo Millonarios vs América")
    await harness.send_text("Castro de América gana los duelos aéreos")
    await harness.send_text("/finalizar")

    harness.outbox.clear()
    await harness.send_text("/reporte_jugador Castro")
    report = harness.outbox.last_text() or ""
    assert "Castro" in report
    assert "Historial" in report and "Resumen" in report
    # Decision buttons offered.
    assert any(d.startswith("decision:") for d in harness.outbox.callback_data())

    # Take a decision via the button.
    decision = next(
        d for d in harness.outbox.callback_data() if d.endswith(":advance")
    )
    await harness.tap_button(decision)
    assert harness.outbox.texts_containing("Decisión registrada: Avanzar")


# ── decisions / edit / merge (Phase 5) ────────────────────────────────────────
@pytest.mark.asyncio
async def test_e2e_decision_command(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    await harness.send_text("Castro de América buen pase")
    harness.outbox.clear()
    await harness.send_text("/decision Castro avanzar")
    assert harness.outbox.texts_containing("decisión")
    assert harness.outbox.texts_containing("Avanzar")


@pytest.mark.asyncio
async def test_e2e_edit_temporary_then_merge_prompt(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    # Create a named prospect first.
    await harness.send_text("Castro de América buen pase")
    # Create a temporary unknown via /foto.
    await harness.send_text("/foto")
    await harness.send_photo()
    await harness.send_text("muy alto, gana por arriba")

    # Name the temp profile as "Castro" (same team) → duplicate-detect should ask.
    harness.outbox.clear()
    await harness.send_text("/editar Jugador nombre=Castro equipo=América")
    assert harness.outbox.texts_containing("parecido")
    merge_btn = next(
        (d for d in harness.outbox.callback_data() if d.startswith("merge:") and d != "merge:cancel"),
        None,
    )
    assert merge_btn is not None
    await harness.tap_button(merge_btn)
    assert harness.outbox.texts_containing("unidos")


@pytest.mark.asyncio
async def test_e2e_merge_command(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    # Two distinct names the scout later realises are the same player.
    await harness.send_text("Castro de América buen pase")
    await harness.send_text("Daniel de América buen control")
    harness.outbox.clear()
    await harness.send_text("/unir Castro | Daniel")
    assert harness.outbox.texts_containing("Unir")
    keep_btn = next(
        d for d in harness.outbox.callback_data()
        if d.startswith("merge:") and d != "merge:cancel"
    )
    await harness.tap_button(keep_btn)
    assert harness.outbox.texts_containing("unidos")


# ── end-of-match fuzzy-dedup confirmation ─────────────────────────────────────
def _dedup_yes_button(outbox) -> str | None:
    """The first 'dedup:<keep>:<drop>' (Sí) callback_data, skipping dedup:no."""
    for d in outbox.callback_data():
        if d.startswith("dedup:") and d != "dedup:no":
            return d
    return None


@pytest.mark.asyncio
async def test_e2e_finalize_asks_dedup_then_merges_and_sends_csv(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    # Two near-name same-team prospects (distinct first tokens that fuzzy-match).
    await harness.send_text("Castro de América buen pase")
    await harness.send_text("Castrillo de América buen control")

    harness.outbox.clear()
    await harness.send_text("/finalizar")
    # Bot asks before finalizing — and no CSV yet.
    assert harness.outbox.texts_containing("son el mismo jugador")
    assert _dedup_yes_button(harness.outbox) is not None
    assert harness.outbox.documents_sent() == []

    # Confirm they're the same → merge, THEN the report is sent.
    pick = _dedup_yes_button(harness.outbox)
    await harness.tap_button(pick)
    assert harness.outbox.texts_containing("unidos")
    docs = harness.outbox.documents_sent()
    assert len(docs) == 1 and docs[0][0] == "informe_Millonarios_vs_América.csv"


@pytest.mark.asyncio
async def test_e2e_finalize_dedup_no_keeps_distinct_still_sends_report(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    await harness.send_text("Castro de América buen pase")
    await harness.send_text("Castrillo de América buen control")

    harness.outbox.clear()
    await harness.send_text("/finalizar")
    assert harness.outbox.texts_containing("son el mismo jugador")

    await harness.tap_button("dedup:no")
    assert harness.outbox.texts_containing("distintos")
    # Report still sent even when kept distinct.
    assert len(harness.outbox.documents_sent()) == 1


@pytest.mark.asyncio
async def test_e2e_finalize_no_dups_finalizes_immediately(harness):
    await harness.send_text("/nuevo Millonarios vs América")
    await harness.send_text("Castro de América buen pase")
    harness.outbox.clear()
    await harness.send_text("/finalizar")
    # No near-duplicate pair → no dedup question, CSV sent straight away.
    assert not harness.outbox.texts_containing("son el mismo jugador")
    assert len(harness.outbox.documents_sent()) == 1


# ── webhook authentication (HTTP layer) ───────────────────────────────────────
@pytest.mark.asyncio
async def test_e2e_webhook_rejects_wrong_token(harness):
    update = message_update(update_id=1, message_id=1, text="/nuevo A vs B")
    resp = await harness.client.post("/telegram/not-the-token", json=update)
    assert resp.status_code == 404
    assert harness.outbox.calls == []


@pytest.mark.asyncio
async def test_e2e_webhook_secret_header_enforced(harness):
    from scouting_bot.config import settings

    update = message_update(update_id=1, message_id=1, text="/nuevo A vs B")
    original = settings.webhook_secret
    object.__setattr__(settings, "webhook_secret", "s3cret")
    try:
        bad = await harness.client.post(f"/telegram/{harness.token}", json=update)
        assert bad.status_code == 403

        bad2 = await harness.client.post(
            f"/telegram/{harness.token}",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert bad2.status_code == 403
        assert harness.outbox.calls == []

        ok = await harness.client.post(
            f"/telegram/{harness.token}",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        )
        assert ok.status_code == 200
        assert harness.outbox.texts_containing("Partido iniciado")
    finally:
        object.__setattr__(settings, "webhook_secret", original)
