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
