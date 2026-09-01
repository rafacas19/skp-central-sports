"""The one-off pass that splits already-stored team names into club + category.

Covers the two halves that matter: the plan is honest about what it will do
(dry run writes nothing) and applying it merges the records that become the same
player once the category leaves the team name.
"""

import pytest

from scouting_bot.backfill import apply_backfill, format_plan, plan_backfill
from scouting_bot.models import Observation, Prospect, Session


async def _seed_unsplit() -> dict:
    """Rows as they exist before the change: category baked into the name."""
    match = await Session.create(
        agent_chat_id=1, home_team="Santa Fe U18", away_team="Millonarios", state="ended"
    )
    # The same player captured under both spellings — two records today.
    old = await Prospect.create(
        agent_chat_id=1, name="Pérez", normalized_name="perez",
        team="Santa Fe", normalized_team="santa fe",
    )
    new = await Prospect.create(
        agent_chat_id=1, name="Pérez", normalized_name="perez",
        team="Santa Fe U18", normalized_team="santa fe u18",
    )
    other = await Prospect.create(
        agent_chat_id=1, name="Gómez", normalized_name="gomez",
        team="Millonarios", normalized_team="millonarios",
    )
    await Observation.create(session=match, prospect=new, raw_quote="Gol de cabeza", minute=20)
    return {"match": match, "old": old, "new": new, "other": other}


@pytest.mark.asyncio
async def test_plan_lists_the_changes_without_writing(storage):
    seeded = await _seed_unsplit()
    plan = await plan_backfill()

    assert [s.id for s in plan.sessions] == [seeded["match"].id]
    assert plan.sessions[0].new_home_team == "Santa Fe"
    assert plan.sessions[0].home_category == "Sub-18"
    assert plan.sessions[0].away_category is None

    changed = {p.id: p for p in plan.prospects}
    assert seeded["new"].id in changed and seeded["other"].id not in changed
    assert changed[seeded["new"].id].merge_into == seeded["old"].id  # oldest survives

    # Nothing was touched.
    assert (await Session.get(id=seeded["match"].id)).home_team == "Santa Fe U18"
    assert (await Prospect.get(id=seeded["new"].id)).category is None
    assert await Prospect.all().count() == 3

    text = format_plan(plan)
    assert "Santa Fe" in text and "Sub-18" in text and "fusiona" in text


@pytest.mark.asyncio
async def test_apply_splits_names_and_merges_the_duplicates(storage):
    seeded = await _seed_unsplit()
    await apply_backfill(await plan_backfill())

    match = await Session.get(id=seeded["match"].id)
    assert match.home_team == "Santa Fe" and match.home_team_category == "Sub-18"
    assert match.away_team == "Millonarios" and match.away_team_category is None

    # The two "Pérez" records are one; the observation moved onto the survivor,
    # which inherited the category from the record that carried it.
    survivor = await Prospect.get(id=seeded["old"].id)
    assert survivor.team == "Santa Fe" and survivor.category == "Sub-18"
    assert await Prospect.filter(id=seeded["new"].id).count() == 0
    assert await Observation.filter(prospect_id=survivor.id).count() == 1

    # An untouched player stays untouched.
    other = await Prospect.get(id=seeded["other"].id)
    assert other.team == "Millonarios" and other.category is None


@pytest.mark.asyncio
async def test_re_running_is_a_no_op(storage):
    await _seed_unsplit()
    await apply_backfill(await plan_backfill())
    after_first = await Prospect.all().count()

    second = await plan_backfill()
    assert second.empty
    await apply_backfill(second)
    assert await Prospect.all().count() == after_first


@pytest.mark.asyncio
async def test_plan_counts_active_sessions(storage):
    await Session.create(agent_chat_id=1, home_team="Cali Sub-20", away_team="Pasto")
    plan = await plan_backfill()
    assert plan.active_sessions == 1
