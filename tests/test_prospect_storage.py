"""Phase 1: cross-match Prospect identity + scout name storage."""

import pytest

from scouting_bot.models import Observation


@pytest.mark.asyncio
async def test_same_name_team_across_sessions_is_one_prospect(service):
    """The same player seen in two matches (different shirt numbers) is one
    prospect, keyed by (chat, normalized name, normalized team)."""
    chat = 100
    s1, _ = await service.start_session(chat, "Millonarios", "América", None)
    s2, _ = await service.start_session(chat, "América", "Nacional", None)  # later

    p1 = await service.storage.get_or_create_prospect(chat, "Castro", "América")
    p2 = await service.storage.get_or_create_prospect(chat, "castro", "america")
    assert p1.id == p2.id  # accent/case-insensitive, same record

    # Different chat → different owner → different prospect.
    other = await service.storage.get_or_create_prospect(999, "Castro", "América")
    assert other.id != p1.id


@pytest.mark.asyncio
async def test_temp_prospect_is_match_scoped(service):
    chat = 101
    s1, _ = await service.start_session(chat, "A", "B", None)
    t1 = await service.storage.get_or_create_temp_prospect(chat, s1.id, "A", 7)
    t1b = await service.storage.get_or_create_temp_prospect(chat, s1.id, "A", 7)
    assert t1.id == t1b.id and t1.is_temporary

    s2, _ = await service.start_session(chat + 1, "A", "B", None)
    t2 = await service.storage.get_or_create_temp_prospect(chat + 1, s2.id, "A", 7)
    assert t2.id != t1.id  # scoped to its own match


@pytest.mark.asyncio
async def test_find_prospects_by_name_is_fuzzy(service):
    chat = 102
    await service.storage.get_or_create_prospect(chat, "Pérez", "América")
    await service.storage.get_or_create_prospect(chat, "Romero", "Nacional")
    hits = await service.storage.find_prospects_by_name(chat, "perez")
    assert {p.name for p in hits} == {"Pérez"}


@pytest.mark.asyncio
async def test_merge_prospects_repoints_observations(service):
    chat = 103
    s, _ = await service.start_session(chat, "A", "B", None)
    # Two genuinely distinct prospects (not a bare-initial variant, which would
    # now collapse to one — see test_embellished_name_reuses_same_prospect).
    keep = await service.storage.get_or_create_prospect(chat, "Castro", "A")
    drop = await service.storage.get_or_create_prospect(chat, "Castrillo", "A")
    await service.storage.add_observation(
        Observation(session_id=s.id, prospect_id=drop.id, raw_quote="buen pase")
    )
    await service.storage.merge_prospects(keep.id, drop.id)

    assert await service.storage.get_prospect(drop.id) is None
    obs = await service.storage.observations_for_prospect(chat, keep.id)
    assert len(obs) == 1 and obs[0].raw_quote == "buen pase"


@pytest.mark.asyncio
async def test_scout_name_set_and_get(service):
    chat = 104
    assert await service.storage.get_scout_name(chat) is None
    await service.storage.set_scout_name(chat, "Rafa")
    assert await service.storage.get_scout_name(chat) == "Rafa"
    await service.storage.set_scout_name(chat, "Rafael")  # update
    assert await service.storage.get_scout_name(chat) == "Rafael"


# ── Fix 2: identity normalization (no name fragmentation) ─────────────────────
def test_normalize_identity_collapses_trailing_initial():
    from scouting_bot.taxonomy import normalize_identity

    assert normalize_identity("Castro B.") == "castro"
    assert normalize_identity("Castro") == "castro"
    assert normalize_identity("C. Castro") == "c castro"  # leading initial kept
    assert normalize_identity("Daniel Castro") == "daniel castro"
    # A genuinely different surname stays distinct (no fuzzy collapse).
    assert normalize_identity("Castrillo") == "castrillo"


@pytest.mark.asyncio
async def test_embellished_name_reuses_same_prospect(service):
    """'Castro B.' must key to the same prospect as 'Castro' (same team)."""
    chat = 106
    p1 = await service.storage.get_or_create_prospect(chat, "Castro", "América")
    p2 = await service.storage.get_or_create_prospect(chat, "Castro B.", "América")
    assert p1.id == p2.id  # no duplicate row

    # But a different surname is still its own prospect.
    other = await service.storage.get_or_create_prospect(chat, "Castrillo", "América")
    assert other.id != p1.id


@pytest.mark.asyncio
async def test_update_prospect_fields(service):
    chat = 105
    p = await service.storage.get_or_create_prospect(chat, "Castro", "América")
    await service.storage.update_prospect(
        p.id, age=22, height_cm=180, latest_rating=4.5, decision_status="Avanzar"
    )
    refreshed = await service.storage.get_prospect(p.id)
    assert refreshed.age == 22
    assert refreshed.height_cm == 180
    assert refreshed.latest_rating == 4.5
    assert refreshed.decision_status == "Avanzar"
