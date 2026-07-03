"""Match clock: /primer_tiempo, /segundo_tiempo, minute derivation, re-sync."""

from datetime import timedelta, timezone, datetime

import pytest

from scouting_bot.service import extract_inline_minute


def _t(minute_offset: float, base=None):
    base = base or datetime(2026, 6, 30, 20, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minute_offset)


def _ago(minutes: float):
    """Real wall-clock time `minutes` ago — used when the code path computes the
    minute against the real clock (capture_notes doesn't take a `now`)."""
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


# ── pure minute extraction ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,expected_min,expected_clean",
    [
        ("Ferrin gol min 37", 37, "Ferrin gol"),
        ("Ferrin gol minuto 37", 37, "Ferrin gol"),
        ("remate 12'", 12, "remate"),
        ("min. 5 buen arranque", 5, "buen arranque"),
        ("sin minuto aquí", None, "sin minuto aquí"),
        ("min 200 fuera de rango", None, "min 200 fuera de rango"),
    ],
)
def test_extract_inline_minute(text, expected_min, expected_clean):
    cleaned, minute = extract_inline_minute(text)
    assert minute == expected_min
    assert cleaned == expected_clean


# ── clock lifecycle ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_clock_not_running_after_nuevo(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    assert service.current_minute(sess, _t(30)) is None  # /nuevo doesn't start it


@pytest.mark.asyncio
async def test_first_half_minute_counts_from_zero(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    start = _t(0)
    await service.start_first_half(sess, now=start)
    assert service.current_minute(sess, now=start) == 0
    assert service.current_minute(sess, now=_t(12, start)) == 12
    # Keeps counting past 45 (added time) — no auto-pause.
    assert service.current_minute(sess, now=_t(47, start)) == 47


@pytest.mark.asyncio
async def test_second_half_resumes_at_45(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    await service.start_first_half(sess, now=_t(0))
    kickoff2 = _t(60)  # scout starts 2nd half after the break
    await service.start_second_half(sess, now=kickoff2)
    assert service.current_minute(sess, now=kickoff2) == 45
    assert service.current_minute(sess, now=_t(15, kickoff2)) == 60
    # Second half keeps counting past 90 too.
    assert service.current_minute(sess, now=_t(46, kickoff2)) == 91


@pytest.mark.asyncio
async def test_resync_first_half(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    start = _t(0)
    await service.start_first_half(sess, now=start)
    # Wall clock says 30 but the real match is at 37 (slow kickoff). Re-sync.
    at = _t(30, start)
    await service.resync_clock(sess, 37, now=at)
    assert service.current_minute(sess, now=at) == 37
    assert service.current_minute(sess, now=_t(1, at)) == 38


@pytest.mark.asyncio
async def test_resync_second_half(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    await service.start_first_half(sess, now=_t(0))
    kickoff2 = _t(60)
    await service.start_second_half(sess, now=kickoff2)
    at = _t(10, kickoff2)  # wall-clock minute 55
    await service.resync_clock(sess, 70, now=at)
    assert service.current_minute(sess, now=at) == 70


# ── minute stamped on observations ──────────────────────────────────────────
async def _capture(service, sess, text):
    for r in await service.capture_notes(sess, text):
        if r.needs_team_choice:
            await service.resolve_team_choice(
                sess, r.classified, r.team_candidates[0], minute=r.minute
            )


@pytest.mark.asyncio
async def test_observation_has_no_minute_when_clock_stopped(service):
    sess, _ = await service.start_session(1, "América", "Nacional", None)
    await _capture(service, sess, "Castro de América buen pase")
    obs = await service.storage.list_observations(sess.id)
    assert obs[-1].minute is None  # clock never started


@pytest.mark.asyncio
async def test_observation_stamped_with_current_minute(service):
    sess, _ = await service.start_session(1, "América", "Nacional", None)
    # Start the clock 12 minutes ago (real time) so "now" reads minute 12.
    await service.start_first_half(sess, now=_ago(12))
    await _capture(service, sess, "Castro de América buen pase")
    obs = await service.storage.list_observations(sess.id)
    assert obs[-1].minute == 12


@pytest.mark.asyncio
async def test_inline_minute_overrides_and_resyncs(service):
    sess, _ = await service.start_session(1, "América", "Nacional", None)
    await service.start_first_half(sess, now=_ago(12))  # wall-clock ≈ 12
    # Scout writes an explicit minute → that obs is stamped 37 AND clock re-syncs.
    await _capture(service, sess, "Castro de América gol min 37")
    obs = await service.storage.list_observations(sess.id)
    assert obs[-1].minute == 37
    assert obs[-1].raw_quote == "Castro de América gol"  # minute stripped
    # The running clock is now anchored at ~37, so it reads >= 37 immediately after.
    assert service.current_minute(sess) >= 37
