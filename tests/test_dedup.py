"""End-of-match fuzzy-dedup detection (service + storage)."""

import pytest

from scouting_bot.models import Observation


async def _capture(service, sess, text):
    for r in await service.capture_notes(sess, text):
        if r.needs_team_choice:
            await service.resolve_team_choice(sess, r.classified, r.team_candidates[0])


@pytest.mark.asyncio
async def test_prospects_in_session_named_distinct_only(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    await _capture(service, sess, "Castro de A buen pase")        # named
    await _capture(service, sess, "#9 de A definición")            # temp (number-only)
    await service.add_team_note(sess, "presionan alto", "A")       # team note
    sess = await service.storage.get_session(sess.id)

    named = await service.storage.prospects_in_session(sess.id)
    assert [p.name for p in named] == ["Castro"]  # excludes temp + team note


@pytest.mark.asyncio
async def test_find_dedup_pairs_pairs_fuzzy_same_team(service):
    sess, _ = await service.start_session(1, "América", "Nacional", None)
    # Two distinct first-token names that fuzzily match, same team.
    await _capture(service, sess, "Castro de América buen pase")
    await _capture(service, sess, "Castrillo de América buen control")
    sess = await service.storage.get_session(sess.id)

    pairs = await service.find_dedup_pairs(sess)
    assert len(pairs) == 1
    keep, drop = pairs[0]
    assert {keep.name, drop.name} == {"Castro", "Castrillo"}
    assert keep.id < drop.id  # keep = earliest identity


@pytest.mark.asyncio
async def test_find_dedup_pairs_skips_different_teams(service):
    sess, _ = await service.start_session(1, "América", "Nacional", None)
    await _capture(service, sess, "Castro de América buen pase")
    await _capture(service, sess, "Castrillo de Nacional buen control")
    sess = await service.storage.get_session(sess.id)
    assert await service.find_dedup_pairs(sess) == []  # same-team guard


@pytest.mark.asyncio
async def test_find_dedup_pairs_skips_unrelated_names(service):
    sess, _ = await service.start_session(1, "América", "Nacional", None)
    await _capture(service, sess, "Castro de América buen pase")
    await _capture(service, sess, "Daniel de América buen control")
    sess = await service.storage.get_session(sess.id)
    assert await service.find_dedup_pairs(sess) == []  # names don't fuzzy-match
