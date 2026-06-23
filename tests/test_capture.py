"""Observation-first capture: identity extraction → on-the-fly prospects.

No roster, no lineup gate. The deterministic MockAIProvider extracts identity
(name / number / position / team) from the text; the service resolves it to a
cross-match prospect, or asks which team for a number-only note."""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def match(service):
    """An active match, ready for observations (no roster step)."""
    sess, _ = await service.start_session(1, "Millonarios", "América", None)
    return sess


async def _capture_one(service, session, text):
    results = await service.capture_notes(session, text)
    assert len(results) == 1
    return results[0]


@pytest.mark.asyncio
async def test_named_observation_creates_prospect(service, match):
    result = await _capture_one(service, match, "Castro, volante, América, se asocia bien")
    assert not result.needs_team_choice
    assert result.prospect is not None
    assert result.prospect.name == "Castro"
    assert result.prospect.team == "América"
    assert result.observation.player_name == "Castro"
    assert result.observation.team == "América"
    # No automatic evaluation is stored (identity only; rating is manual).
    assert result.observation.rating is None


@pytest.mark.asyncio
async def test_number_with_team_creates_temp_prospect(service, match):
    result = await _capture_one(service, match, "#10 de Millonarios tiene buen pase filtrado")
    assert not result.needs_team_choice
    assert result.prospect is not None
    assert result.prospect.is_temporary
    assert result.prospect.team == "Millonarios"
    assert result.observation.player_number == 10


@pytest.mark.asyncio
async def test_number_only_no_team_asks_for_team(service, match):
    # "#7 muy rápido" — could be either team → don't guess, ask.
    result = await _capture_one(service, match, "#7 muy rápido en el 1vs1")
    assert result.needs_team_choice
    assert result.team_candidates == ["Millonarios", "América"]
    assert result.observation is None


@pytest.mark.asyncio
async def test_resolve_team_choice_stores_note(service, match):
    result = await _capture_one(service, match, "#7 muy rápido")
    obs = await service.resolve_team_choice(match, result.classified, "América")
    assert obs.team == "América"
    assert obs.player_number == 7
    assert obs.prospect_id is not None


@pytest.mark.asyncio
async def test_team_note_has_no_prospect(service, match):
    result = await _capture_one(
        service, match, "América juega con línea de 4 y presionan muy alto"
    )
    assert result.classified.is_team_note
    assert result.prospect is None
    assert result.observation.is_team_note
    assert result.observation.prospect_id is None


@pytest.mark.asyncio
async def test_same_player_two_matches_is_one_prospect(service):
    chat = 7
    m1, _ = await service.start_session(chat, "América", "Nacional", None)
    r1 = await _capture_one(service, m1, "Castro, volante, América, buen pase")
    await service.end_session(m1)
    m2, _ = await service.start_session(chat, "Millonarios", "América", None)
    r2 = await _capture_one(service, m2, "Castro de América gana los duelos")
    assert r1.prospect.id == r2.prospect.id  # one cross-match identity
    obs = await service.storage.observations_for_prospect(chat, r1.prospect.id)
    assert len(obs) == 2


@pytest.mark.asyncio
async def test_source_is_tagged(service, match):
    result = (await service.capture_notes(match, "Castro de América buen pase", source="voice"))[0]
    assert result.observation.source == "voice"


@pytest.mark.asyncio
async def test_corrections_undo(service, match):
    await service.capture_notes(match, "Castro de América buen pase")
    removed = await service.undo_last(match)
    assert removed is not None
    assert await service.storage.last_observation(match.id) is None


# ── multi-player note ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_multi_player_message_splits_into_two_notes(service, match):
    results = await service.capture_notes(
        match, "Castro de América muy bien pero #9 de Millonarios lento"
    )
    assert len(results) == 2
    # Castro → named prospect; #9 de Millonarios → temp prospect; both stored.
    assert all(r.observation is not None for r in results)
    assert all(not r.needs_team_choice for r in results)


@pytest.mark.asyncio
async def test_multi_player_mixes_stored_and_team_ambiguous(service, match):
    # Castro (named, América) stored; "#9 too slow" (no team) → asks.
    results = await service.capture_notes(
        match, "Castro de América brillante pero #9 muy lento"
    )
    assert len(results) == 2
    stored = [r for r in results if r.observation is not None]
    asking = [r for r in results if r.needs_team_choice]
    assert len(stored) == 1 and stored[0].prospect.name == "Castro"
    assert len(asking) == 1
