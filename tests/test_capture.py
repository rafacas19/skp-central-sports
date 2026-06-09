import pytest
import pytest_asyncio

from scouting_bot.ai.base import ParsedPlayer


@pytest_asyncio.fixture
async def session_with_roster(service):
    sess, _ = await service.start_session(1, "Boca", "River", None)
    parsed = [
        ParsedPlayer(8, "Vidal", "CM", "home"),
        ParsedPlayer(10, "Sosa", "AM", "home"),
        ParsedPlayer(8, "Mendes", "CM", "away"),  # number clash on purpose
        ParsedPlayer(3, "Romero", "LB", "home"),
    ]
    await service.save_roster(sess, parsed)
    await service.confirm_roster(sess)
    return await service.storage.get_session(sess.id)


async def _capture_one(service, session, text):
    """Helper: a single-reference message yields exactly one CaptureResult."""
    results = await service.capture_notes(session, text)
    assert len(results) == 1
    return results[0]


@pytest.mark.asyncio
async def test_name_reference_is_confident(service, session_with_roster):
    result = await _capture_one(service, session_with_roster, "Sosa great vision and passing")
    assert not result.needs_disambiguation
    assert result.matched_player.name == "Sosa"
    assert result.observation.sentiment == "positive"
    assert result.observation.skill_category in {"passing", "decision_making"}


@pytest.mark.asyncio
async def test_ambiguous_number_triggers_disambiguation(service, session_with_roster):
    # #8 exists on BOTH teams → must ask.
    result = await _capture_one(service, session_with_roster, "number 8 poor first touch")
    assert result.needs_disambiguation
    assert {p.name for p in result.candidates} == {"Vidal", "Mendes"}
    assert result.observation is None


@pytest.mark.asyncio
async def test_disambiguation_resolution_stores_note(service, session_with_roster):
    result = await _capture_one(service, session_with_roster, "number 8 slow on the turn")
    chosen = next(p for p in result.candidates if p.name == "Vidal")
    obs = await service.resolve_disambiguation(session_with_roster, result.classified, chosen)
    assert obs.player_id == chosen.id
    assert obs.sentiment == "negative"


@pytest.mark.asyncio
async def test_unique_number_is_confident(service, session_with_roster):
    result = await _capture_one(service, session_with_roster, "number 10 brilliant finish")
    assert not result.needs_disambiguation
    assert result.matched_player.name == "Sosa"


@pytest.mark.asyncio
async def test_team_note_has_no_player(service, session_with_roster):
    result = await _capture_one(
        service, session_with_roster, "the team is pressing high and leaving space"
    )
    assert not result.needs_disambiguation
    assert result.classified.is_team_note
    assert result.observation.player_id is None


@pytest.mark.asyncio
async def test_corrections(service, session_with_roster):
    await service.capture_notes(session_with_roster, "number 10 brilliant finish")
    flipped = await service.flip_last_sentiment(session_with_roster)
    assert flipped == "negative"
    removed = await service.undo_last(session_with_roster)
    assert removed is not None
    assert await service.storage.last_observation(session_with_roster.id) is None


@pytest.mark.asyncio
async def test_roster_gap_add_player(service, session_with_roster):
    p = await service.add_missing_player(session_with_roster, "home", 14, "Gómez", "CB")
    assert p.id is not None
    listed = await service.storage.list_players(session_with_roster.id)
    assert any(pl.name == "Gómez" for pl in listed)


# ── fuzzy name matching (Feature 3) ───────────────────────────────────────────
@pytest_asyncio.fixture
async def session_fuzzy_roster(service):
    """Roster with an accented name and a typo-prone surname for name matching."""
    sess, _ = await service.start_session(2, "Alpha", "Beta", None)
    parsed = [
        ParsedPlayer(4, "Pérez", "CB", "home"),
        ParsedPlayer(10, "Lionel Messi", "AM", "home"),
        ParsedPlayer(8, "Mendes", "CM", "away"),
    ]
    await service.save_roster(sess, parsed)
    await service.confirm_roster(sess)
    return await service.storage.get_session(sess.id)


@pytest.mark.asyncio
async def test_name_match_is_accent_insensitive(service, session_fuzzy_roster):
    result = await _capture_one(service, session_fuzzy_roster, "perez strong in the air")
    assert not result.needs_disambiguation
    assert result.matched_player.name == "Pérez"


@pytest.mark.asyncio
async def test_name_match_accepts_surname_only(service, session_fuzzy_roster):
    result = await _capture_one(service, session_fuzzy_roster, "Messi brilliant vision")
    assert not result.needs_disambiguation
    assert result.matched_player.name == "Lionel Messi"


@pytest.mark.asyncio
async def test_name_match_tolerates_typo(service, session_fuzzy_roster):
    # 'Mendez' (typo) → Mendes, and name must win even though #8 also exists.
    result = await _capture_one(service, session_fuzzy_roster, "Mendez too slow on the turn")
    assert not result.needs_disambiguation
    assert result.matched_player.name == "Mendes"


# ── multi-player note (Feature 4) ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_multi_player_message_splits_into_two_notes(service, session_with_roster):
    results = await service.capture_notes(
        session_with_roster, "Sosa great vision but Mendes too slow"
    )
    assert len(results) == 2
    assert all(not r.needs_disambiguation for r in results)
    matched = {r.matched_player.name for r in results}
    assert matched == {"Sosa", "Mendes"}
    # Both were stored as observations.
    assert all(r.observation is not None for r in results)


@pytest.mark.asyncio
async def test_multi_player_mixes_confident_and_ambiguous(service, session_with_roster):
    # Sosa is unique (confident); '#8' is shared by Vidal & Mendes (ambiguous).
    results = await service.capture_notes(
        session_with_roster, "Sosa brilliant finish but number 8 too slow"
    )
    assert len(results) == 2
    confident = [r for r in results if not r.needs_disambiguation]
    ambiguous = [r for r in results if r.needs_disambiguation]
    assert len(confident) == 1 and confident[0].matched_player.name == "Sosa"
    assert len(ambiguous) == 1
    assert {p.name for p in ambiguous[0].candidates} == {"Vidal", "Mendes"}
