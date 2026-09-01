import pytest

from scouting_bot.models import Observation


@pytest.mark.asyncio
async def test_one_active_session_per_agent(service):
    s1, existing = await service.start_session(111, "A", "B", None)
    assert s1 is not None and existing is None

    s2, existing = await service.start_session(111, "C", "D", None)
    assert s2 is None and existing is not None
    assert existing.id == s1.id


@pytest.mark.asyncio
async def test_session_survives_reload(storage):
    sess = await storage.create_session(222, "A", "B", "Liga")
    # Simulate "restart": fetch fresh from the database.
    again = await storage.get_active_session(222)
    assert again is not None
    assert again.id == sess.id
    assert again.home_team == "A"


@pytest.mark.asyncio
async def test_observation_crud(storage):
    sess = await storage.create_session(1, "A", "B", None)
    obs = await storage.add_observation(
        Observation(
            session_id=sess.id,
            prospect_id=None,
            raw_quote="buen pase",
            source="text",
        )
    )
    assert obs.id is not None
    last = await storage.last_observation(sess.id)
    assert last.raw_quote == "buen pase"
    await storage.delete_observation(obs.id)
    assert await storage.last_observation(sess.id) is None


@pytest.mark.asyncio
async def test_stale_sessions_detection(storage):
    from datetime import datetime, timezone

    await storage.create_session(1, "A", "B", None)
    future = datetime(2999, 1, 1, tzinfo=timezone.utc)
    assert len(await storage.stale_active_sessions(future)) == 1
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert await storage.stale_active_sessions(past) == []


# ── Team categories split off the team name ──────────────────────────────
@pytest.mark.asyncio
async def test_session_teams_are_split_into_club_and_category(storage):
    sess = await storage.create_session(
        1, "Santa Fe U18", "Millonarios Sub-18", None, category="Torneo Sub-18"
    )
    assert sess.home_team == "Santa Fe" and sess.home_team_category == "Sub-18"
    assert sess.away_team == "Millonarios" and sess.away_team_category == "Sub-18"
    # The scout-typed /nuevo metadata is a different field and stays untouched.
    assert sess.category == "Torneo Sub-18"


@pytest.mark.asyncio
async def test_session_team_without_a_category_is_untouched(storage):
    sess = await storage.create_session(1, "Millonarios", "U de Chile", None)
    assert sess.home_team == "Millonarios" and sess.home_team_category is None
    assert sess.away_team == "U de Chile" and sess.away_team_category is None


@pytest.mark.asyncio
async def test_prospect_team_is_split_into_club_and_category(storage):
    p = await storage.get_or_create_prospect(1, "Pérez", "Santa Fe U18")
    assert p.team == "Santa Fe" and p.category == "Sub-18"
    assert p.normalized_team == "santa fe"


@pytest.mark.asyncio
async def test_both_team_spellings_reach_one_prospect(storage):
    """The point of splitting: identity keys on the club, not on the spelling."""
    first = await storage.get_or_create_prospect(1, "Pérez", "Santa Fe U18")
    again = await storage.get_or_create_prospect(1, "Pérez", "Santa Fe")
    assert again.id == first.id


@pytest.mark.asyncio
async def test_category_fills_in_on_a_later_note(storage):
    """A player first seen without the category gets it when it shows up."""
    first = await storage.get_or_create_prospect(1, "Pérez", "Santa Fe")
    assert first.category is None
    again = await storage.get_or_create_prospect(1, "Pérez", "Santa Fe Sub-18")
    assert again.id == first.id and again.category == "Sub-18"


@pytest.mark.asyncio
async def test_temporary_prospect_carries_the_category(storage):
    sess = await storage.create_session(1, "Santa Fe", "Millonarios", None)
    temp = await storage.get_or_create_temp_prospect(1, sess.id, "Santa Fe U18", 7)
    assert temp.team == "Santa Fe" and temp.category == "Sub-18"
    # And the same number in the same match still reuses that one record.
    again = await storage.get_or_create_temp_prospect(1, sess.id, "Santa Fe", 7)
    assert again.id == temp.id
