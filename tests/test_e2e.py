"""End-to-end tests: real Telegram Update JSON → FastAPI webhook → PTB → handlers
→ ScoutingService → Postgres, with only the Telegram network faked.

See tests/e2e_harness.py for how the seam works. The deterministic MockAIProvider
supplies the roster and note classification; its fixed demo roster (ai/mock.py)
is what the assertions below rely on:
  home #10 Sosa (unique name), home #8 Vidal + away #8 Mendes (#8 ambiguous).
"""

import csv
import io

import pytest

from .e2e_harness import message_update


def _pick_data_for(outbox, name_hint: str) -> str | None:
    """Return the `pick:{id}` callback_data whose button label mentions name_hint."""
    for kb in outbox.keyboards():
        for row in kb.inline_keyboard:
            for btn in row:
                if (
                    btn.callback_data
                    and btn.callback_data.startswith("pick:")
                    and name_hint in btn.text
                ):
                    return btn.callback_data
    return None


@pytest.mark.asyncio
async def test_e2e_happy_path_new_photo_confirm_note_finish_csv(harness):
    # 1. Start a match.
    await harness.send_text("/nuevo Boca vs River")
    assert "Sesión iniciada" in (harness.outbox.last_text() or "")
    assert harness.outbox.count_text("Boca") >= 1

    # 2. Lineup photo → staged roster + confirm keyboard.
    await harness.send_photo()
    assert harness.outbox.texts_containing("Alineación detectada")
    assert harness.outbox.texts_containing("Sosa")
    assert "confirm_roster" in harness.outbox.callback_data()

    # 3. Confirm the roster.
    await harness.tap_button("confirm_roster")
    assert harness.outbox.texts_containing("confirmada")
    assert harness.outbox.answered_callbacks() >= 1

    # 4. A confident, unique-name note is stored with a lightweight ack.
    harness.outbox.clear()
    await harness.send_text("Sosa great vision")
    assert any(t.startswith("✅") for t in harness.outbox.texts())

    # 5. End the match → a CSV document with the observation.
    harness.outbox.clear()
    await harness.send_text("/fin")
    docs = harness.outbox.documents_sent()
    assert len(docs) == 1
    filename, content = docs[0]
    assert filename == "informe_Boca_vs_River.csv"
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    header, data = rows[0], rows[1:]
    quote_i = header.index("raw_quote")
    name_i = header.index("player_name")
    assert any(
        r[quote_i] == "Sosa great vision" and r[name_i] == "Sosa" for r in data
    )


@pytest.mark.asyncio
async def test_e2e_multi_image_repeat_photo_does_not_wipe_roster(harness):
    """F1 at the handler level: a second photo must not wipe/replace the staged
    roster. NOTE: the MockAIProvider returns a FIXED 12-player roster regardless
    of the image bytes, so it cannot model 'home-only photo then away-only photo'
    — true cross-team merge is covered by the service-level unit tests in
    tests/test_roster.py. This proves the on_photo → parse → merge → confirm
    wiring survives a repeat photo (no wipe, no duplication)."""
    await harness.send_text("/nuevo Boca vs River")

    await harness.send_photo()
    first = harness.outbox.texts_containing("Alineación detectada")[-1]

    harness.outbox.clear()
    await harness.send_photo()
    second = harness.outbox.texts_containing("Alineación detectada")[-1]

    # Same roster echoed both times — the merge overwrote in place, so the second
    # photo neither wiped the roster nor duplicated any player.
    assert first == second
    for name in ("Sosa", "Vidal", "Mendes"):
        assert name in second


@pytest.mark.asyncio
async def test_e2e_manual_lineup_partial_confirm_and_capture(harness):
    """F2: stage a partial roster via /jugadores, confirm it, capture a note."""
    await harness.send_text("/nuevo Boca vs River")

    await harness.send_text("/jugadores local: 10 Messi DC; visitante: 5 Ramos")
    echo = harness.outbox.texts_containing("Alineación detectada")
    assert echo and "Messi" in echo[-1] and "Ramos" in echo[-1]
    assert "confirm_roster" in harness.outbox.callback_data()

    await harness.tap_button("confirm_roster")
    assert harness.outbox.texts_containing("confirmada")

    harness.outbox.clear()
    await harness.send_text("Messi great finish")
    assert any(t.startswith("✅") for t in harness.outbox.texts())


@pytest.mark.asyncio
async def test_e2e_multi_player_note_and_disambiguation_queue(harness):
    """F4: one message qualifies two players — one confident (Sosa, unique),
    one ambiguous (#8 = Vidal home + Mendes away). The confident one is acked and
    the ambiguous one is queued; resolving it via an inline button drains the
    queue (no further prompt)."""
    await harness.send_text("/nuevo Boca vs River")
    await harness.send_photo()
    await harness.tap_button("confirm_roster")

    harness.outbox.clear()
    await harness.send_text("Sosa brilliant finish but number 8 too slow")

    # Confident note acked, AND a disambiguation prompt for the ambiguous #8.
    assert any(t.startswith("✅") for t in harness.outbox.texts())
    assert harness.outbox.texts_containing("¿A quién te refieres?")
    pick_data = harness.outbox.callback_data()
    # Both #8 candidates offered (plus a skip).
    assert sum(1 for d in pick_data if d.startswith("pick:")) >= 2
    assert "pick:skip" in pick_data

    vidal_pick = _pick_data_for(harness.outbox, "Vidal")
    assert vidal_pick is not None

    # Resolve to Vidal → stored, queue drained (no NEW "¿A quién te refieres?").
    # Don't clear the outbox here: keeping it lets the resolution reply show in
    # the `-s` transcript. Assert the prompt count didn't grow instead.
    prompts_before = harness.outbox.count_text("¿A quién te refieres?")
    await harness.tap_button(vidal_pick)
    assert harness.outbox.texts_containing("Registrada")
    assert harness.outbox.count_text("¿A quién te refieres?") == prompts_before


@pytest.mark.asyncio
async def test_e2e_voice_note_is_transcribed_and_classified(harness):
    """A voice note flows through transcription → classification → capture.
    The mock transcribes every voice note to a fixed line that references '#8',
    which is ambiguous (Vidal home + Mendes away) → the bot asks who it means.
    This exercises the on_voice → transcribe → classify → disambiguate path."""
    await harness.send_text("/nuevo Boca vs River")
    await harness.send_photo()
    await harness.tap_button("confirm_roster")

    harness.outbox.clear()
    await harness.send_voice()

    # The transcript mentions '#8' (shared) → disambiguation prompt with both #8s.
    assert harness.outbox.texts_containing("¿A quién te refieres?")
    pick = harness.outbox.callback_data()
    assert sum(1 for d in pick if d.startswith("pick:")) >= 2

    # Resolving to a candidate stores the transcribed note.
    vidal_pick = _pick_data_for(harness.outbox, "Vidal")
    await harness.tap_button(vidal_pick)
    assert harness.outbox.texts_containing("Registrada")


# ── webhook authentication (HTTP layer) ───────────────────────────────────────
@pytest.mark.asyncio
async def test_e2e_webhook_rejects_wrong_token(harness):
    """A POST to /telegram/{wrong} must 404 — the URL token is a shared secret."""
    update = message_update(update_id=1, message_id=1, text="/nuevo A vs B")
    resp = await harness.client.post("/telegram/not-the-token", json=update)
    assert resp.status_code == 404
    # Nothing was processed.
    assert harness.outbox.calls == []


@pytest.mark.asyncio
async def test_e2e_webhook_secret_header_enforced(harness):
    """When WEBHOOK_SECRET is set, a missing/wrong X-Telegram-Bot-Api-Secret-Token
    header is rejected with 403, and the correct one is accepted."""
    from scouting_bot.config import settings

    update = message_update(update_id=1, message_id=1, text="/nuevo A vs B")
    # settings is a frozen dataclass → mutate via object.__setattr__; restore after.
    original = settings.webhook_secret
    object.__setattr__(settings, "webhook_secret", "s3cret")
    try:
        # Right token in URL but missing header → 403.
        bad = await harness.client.post(f"/telegram/{harness.token}", json=update)
        assert bad.status_code == 403

        # Wrong header value → 403.
        bad2 = await harness.client.post(
            f"/telegram/{harness.token}",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert bad2.status_code == 403
        assert harness.outbox.calls == []  # neither reached a handler

        # Correct header → accepted and processed.
        ok = await harness.client.post(
            f"/telegram/{harness.token}",
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        )
        assert ok.status_code == 200
        assert harness.outbox.texts_containing("Sesión iniciada")
    finally:
        object.__setattr__(settings, "webhook_secret", original)
