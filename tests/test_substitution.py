"""Substitution handling: identify the ENTERING player, flag the observation."""

import pytest

from scouting_bot.ai.mock import MockAIProvider


async def _classify(text, home="Millonarios", away="América"):
    return await MockAIProvider().classify_notes(text, home, away)


# ── classification (mock AI) ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sub_named_in_number_out():
    notes = await _classify("Entra Ferrin y sale el número 7")
    assert len(notes) == 1
    n = notes[0]
    assert n.is_substitution is True
    assert n.player_ref.name == "Ferrin"  # entering player, not the exiting #7


@pytest.mark.asyncio
async def test_sub_named_in_named_out():
    notes = await _classify("Entra Ferrin de Millonarios sale Ocampo")
    assert len(notes) == 1
    assert notes[0].is_substitution is True
    assert notes[0].player_ref.name == "Ferrin"
    assert notes[0].player_ref.team == "Millonarios"


@pytest.mark.asyncio
async def test_sub_number_in_number_out_with_team():
    notes = await _classify("Entra el número 7 de América y sale el número 9")
    assert len(notes) == 1
    n = notes[0]
    assert n.is_substitution is True
    assert n.player_ref.number == 7  # entering number
    assert n.player_ref.team == "América"


@pytest.mark.asyncio
async def test_sub_number_in_no_team_is_ambiguous():
    notes = await _classify("Entra número 7 y sale Ocampo")
    assert len(notes) == 1
    n = notes[0]
    assert n.is_substitution is True
    assert n.player_ref.number == 7
    assert n.player_ref.team is None
    assert n.confidence < 0.6  # team-ambiguous → the bot will ask


# ── service: entering player becomes a prospect, obs flagged ─────────────────
@pytest.mark.asyncio
async def test_substitution_creates_entering_prospect(service):
    sess, _ = await service.start_session(1, "Millonarios", "América", None)
    results = await service.capture_notes(sess, "Entra Ferrin de Millonarios sale Ocampo")
    assert len(results) == 1
    r = results[0]
    assert r.observation.is_substitution is True
    assert r.prospect is not None and r.prospect.name == "Ferrin"

    # A later bare-name observation ("Ferrin …", no team repeated) attaches to the
    # SAME prospect — this is what lets the scout keep noting the substituted-in
    # player without restating the team every time.
    more = await service.capture_notes(sess, "Ferrin buen juego aéreo")
    assert more[0].prospect.id == r.prospect.id
