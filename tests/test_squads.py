"""National squads ("selecciones"): membership beside the club, not instead of it.

The storage-level guarantees live here — a call-up never touches the player's
club, and merging two profiles of one person keeps his call-ups. The pages that
render all this are covered in test_dashboard.py.
"""

from scouting_bot.dashboard import queries
from scouting_bot.models import Observation, Prospect, Session, Squad, SquadMember


async def _player(name: str, team: str, **kw) -> Prospect:
    from scouting_bot.taxonomy import normalize_identity, normalize_name

    return await Prospect.create(
        agent_chat_id=1,
        name=name,
        normalized_name=normalize_identity(name),
        team=team,
        normalized_team=normalize_name(team),
        **kw,
    )


async def _squad(name: str = "Colombia", category: str | None = "Sub-15") -> Squad:
    from scouting_bot.taxonomy import normalize_name

    return await Squad.create(
        agent_chat_id=1,
        name=name,
        normalized_name=normalize_name(name),
        category=category,
    )


# ── Name parsing (pure) ──────────────────────────────────────────────────
def test_squad_name_splits_the_category_and_drops_the_prefix():
    assert queries.squad_name_and_category("Selección Colombia U15") == (
        "Colombia", "Sub-15",
    )
    assert queries.squad_name_and_category("Colombia sub 15") == ("Colombia", "Sub-15")
    assert queries.squad_name_and_category("Seleccion de Colombia Sub-17") == (
        "Colombia", "Sub-17",
    )


def test_squad_without_a_category_keeps_a_blank_one():
    assert queries.squad_name_and_category("Colombia") == ("Colombia", None)


def test_a_squad_called_only_seleccion_keeps_its_name():
    """Stripping the prefix must never leave a nameless squad."""
    assert queries.squad_name_and_category("Selección") == ("Selección", None)


# ── Membership ───────────────────────────────────────────────────────────
async def test_call_up_leaves_the_players_club_alone(storage):
    """The whole point of a membership row: no second record for one player."""
    player = await _player("Yaroll Martinez", "Santa Fe")
    squad = await _squad()
    await SquadMember.create(squad=squad, prospect=player)

    fresh = await Prospect.get(id=player.id)
    assert (fresh.team, fresh.normalized_team) == ("Santa Fe", "santa fe")
    assert await Prospect.all().count() == 1
    assert [s["title"] for s in await queries.player_squads(player.id)] == [
        "Colombia Sub-15"
    ]


async def test_a_player_can_be_in_two_squads_at_once(storage):
    player = await _player("Luis Moreno", "Deportivo Pereira")
    await SquadMember.create(squad=await _squad(category="Sub-15"), prospect=player)
    await SquadMember.create(squad=await _squad(category="Sub-17"), prospect=player)

    assert [s["title"] for s in await queries.player_squads(player.id)] == [
        "Colombia Sub-15", "Colombia Sub-17",
    ]


async def test_squad_options_exclude_the_squads_he_is_already_in(storage):
    player = await _player("Luis Moreno", "Deportivo Pereira")
    joined = await _squad(category="Sub-15")
    await _squad(category="Sub-17")
    await SquadMember.create(squad=joined, prospect=player)

    assert [s["title"] for s in await queries.squad_options_for(player.id)] == [
        "Colombia Sub-17"
    ]


async def test_deleting_a_squad_keeps_its_players(storage):
    player = await _player("Miguel Yepes", "Atlético Nacional")
    squad = await _squad()
    await SquadMember.create(squad=squad, prospect=player)

    await Squad.filter(id=squad.id).delete()

    assert await SquadMember.all().count() == 0
    assert await Prospect.filter(id=player.id).exists()


# ── Merging two profiles of one player ───────────────────────────────────
async def test_merge_moves_the_call_ups_to_the_survivor(storage):
    keep = await _player("Andrés Mena", "Atlético Nacional")
    drop = await _player("Mena", "Atlético Nacional")
    squad = await _squad()
    await SquadMember.create(squad=squad, prospect=drop)

    await storage.merge_prospects(keep.id, drop.id)

    rows = await SquadMember.all()
    assert len(rows) == 1
    assert rows[0].prospect_id == keep.id


async def test_merge_dedupes_a_squad_both_profiles_were_in(storage):
    """Both halves called up ⇒ one membership survives, not a constraint error."""
    keep = await _player("Andrés Mena", "Atlético Nacional")
    drop = await _player("Mena", "Atlético Nacional")
    squad = await _squad()
    other = await _squad(category="Sub-17")
    await SquadMember.create(squad=squad, prospect=keep)
    await SquadMember.create(squad=squad, prospect=drop)
    await SquadMember.create(squad=other, prospect=drop)

    await storage.merge_prospects(keep.id, drop.id)

    titles = [s["title"] for s in await queries.player_squads(keep.id)]
    assert titles == ["Colombia Sub-15", "Colombia Sub-17"]
    assert await SquadMember.all().count() == 2


async def test_merge_still_moves_observations_when_no_squads_exist(storage):
    """The squad step must not disturb the merge for everyone else."""
    session = await Session.create(agent_chat_id=1, home_team="A", away_team="B")
    keep = await _player("Andrés Mena", "Atlético Nacional")
    drop = await _player("Mena", "Atlético Nacional")
    await Observation.create(session=session, prospect=drop, raw_quote="Buen pase")

    await storage.merge_prospects(keep.id, drop.id)

    assert await Observation.filter(prospect_id=keep.id).count() == 1
    assert not await Prospect.filter(id=drop.id).exists()
