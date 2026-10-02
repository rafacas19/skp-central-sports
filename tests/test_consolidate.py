"""Consolidating test chats into the scout's chat, and the panel's owner chat."""

from datetime import datetime, timezone

import pytest

from scouting_bot.config import settings
from scouting_bot.consolidate import Refused, plan_and_run
from scouting_bot.dashboard import queries
from scouting_bot.evaluations import save_evaluation
from scouting_bot.models import (
    ORIGIN_WEB,
    Evaluation,
    MatchPlayer,
    Observation,
    Prospect,
    Session,
    Squad,
    SquadMember,
)
from scouting_bot.profiles import get_profile

SCOUT, TEST, DEV = 7000, 5000, 6000


async def _world(storage):
    """Scout chat with a player; a test chat holding a duplicate of him, a
    player of its own, a web match, a squad; a dev chat with a bare match."""
    when = datetime(2026, 9, 1, tzinfo=timezone.utc)
    scout_match = await storage.create_session(SCOUT, "Junior", "Nacional", None, match_date=when)
    camilo = await storage.get_or_create_prospect(SCOUT, "Camilo Restrepo", "Junior")
    await Observation.create(session=scout_match, prospect=camilo, raw_quote="Buen partido", rating=3)
    await storage.end_session(scout_match.id)

    web = await storage.create_session(TEST, "Junior", "Cali", None, origin=ORIGIN_WEB, match_date=when)
    twin = await storage.get_or_create_prospect(TEST, "Camilo Restrepo", "Junior")
    luis = await storage.get_or_create_prospect(TEST, "Luis Arias", "Junior")
    await MatchPlayer.create(session=web, prospect=twin, side="home", slot=1)
    await save_evaluation(web, twin, profile=get_profile("lateral"), raw_scores={"1.1": 5})
    await save_evaluation(web, luis, profile=get_profile("lateral"), raw_scores={"1.1": 3})
    squad = await Squad.create(agent_chat_id=TEST, name="Colombia", normalized_name="colombia", category="Sub-17")
    await SquadMember.create(squad=squad, prospect=twin)

    dev = await storage.create_session(DEV, "River", "Flamengo", None)
    await storage.end_session(dev.id)
    return {"camilo": camilo, "twin": twin, "luis": luis, "web": web, "dev": dev, "squad": squad}


async def test_a_dry_run_reports_and_changes_nothing(storage):
    w = await _world(storage)
    report = await plan_and_run(SCOUT, [TEST], [DEV], apply=False)
    text = "\n".join(report)
    assert "merge chat 5000: 1 matches" in text and "1 moved, 1 merged" in text
    assert f"merge player {w['twin'].id}" in text and "drop chat 6000: delete 1 matches" in text
    assert report[-1].startswith("DRY RUN")
    assert await Session.filter(agent_chat_id=TEST).count() == 1
    assert await Prospect.filter(id=w["twin"].id).exists()


async def test_apply_moves_merges_and_drops_without_losing_data(storage):
    w = await _world(storage)
    sheets, notes = await Evaluation.all().count(), await Observation.all().count()
    await plan_and_run(SCOUT, [TEST], [DEV], apply=True)

    assert set(await Session.all().values_list("agent_chat_id", flat=True)) == {SCOUT}
    assert not await Session.filter(id=w["dev"].id).exists()        # dev match dropped
    assert not await Prospect.filter(id=w["twin"].id).exists()      # duplicate merged away
    camilo = w["camilo"]
    assert await Evaluation.filter(prospect_id=camilo.id).count() == 1   # his web sheet moved
    assert await MatchPlayer.filter(prospect_id=camilo.id).exists()
    assert await SquadMember.filter(prospect_id=camilo.id).exists()
    assert (await Prospect.get(id=w["luis"].id)).agent_chat_id == SCOUT
    assert (await Squad.get(id=w["squad"].id)).agent_chat_id == SCOUT
    assert await Evaluation.all().count() == sheets           # nothing lost
    assert await Observation.all().count() == notes           # (the dev match had none)


async def test_squads_with_the_same_name_are_merged(storage):
    await _world(storage)
    existing = await Squad.create(agent_chat_id=SCOUT, name="Colombia", normalized_name="colombia", category="Sub-17")
    await plan_and_run(SCOUT, [TEST], [], apply=True)
    assert await Squad.all().count() == 1
    assert await SquadMember.filter(squad_id=existing.id).count() == 1


async def test_it_refuses_contradictory_or_unsafe_requests(storage):
    with pytest.raises(Refused):
        await plan_and_run(SCOUT, [SCOUT], [], apply=False)
    with pytest.raises(Refused):
        await plan_and_run(SCOUT, [TEST], [TEST], apply=False)
    # A dropped chat's player with data in a match that is not being dropped.
    w = await _world(storage)
    stray = await storage.get_or_create_prospect(DEV, "Pedro Gil", "River")
    await Observation.create(session=w["web"], prospect=stray, raw_quote="nota")
    with pytest.raises(Refused):
        await plan_and_run(SCOUT, [], [DEV], apply=True)
    assert await Session.filter(id=w["dev"].id).exists()   # the transaction rolled back


async def test_the_panel_owner_chat_is_stable(storage):
    old = settings.owner_chat_id
    try:
        object.__setattr__(settings, "owner_chat_id", 0)
        for _ in range(3):
            await storage.end_session((await storage.create_session(SCOUT, "Junior", "Nacional", None)).id)
        await storage.create_session(TEST, "Junior", "Cali", None)  # the newest match: a test chat
        assert await queries.scout_chat_id() == SCOUT  # most bot matches, not the latest
        object.__setattr__(settings, "owner_chat_id", 4242)
        assert await queries.scout_chat_id() == 4242
    finally:
        object.__setattr__(settings, "owner_chat_id", old)
