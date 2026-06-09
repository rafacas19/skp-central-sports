import pytest

from scouting_bot.ai.base import ParsedPlayer
from scouting_bot.bot import _parse_manual_lineup
from scouting_bot.models import HOME


def _by_side_number(players):
    return {(p.side, p.number): p.name for p in players}


@pytest.mark.asyncio
async def test_merge_roster_unions_two_teams(service):
    """A lineup arriving across two photos (one team each) should accumulate."""
    sess, _ = await service.start_session(1, "Boca", "River", None)

    # First photo: home team only.
    await service.merge_roster(
        sess,
        [
            ParsedPlayer(8, "Vidal", "CM", HOME),
            ParsedPlayer(10, "Sosa", "AM", HOME),
        ],
    )
    sess = await service.storage.get_session(sess.id)

    # Second photo: away team only — must NOT wipe the home team.
    await service.merge_roster(
        sess,
        [
            ParsedPlayer(8, "Mendes", "CM", "away"),
            ParsedPlayer(7, "Rocha", "RW", "away"),
        ],
    )
    sess = await service.storage.get_session(sess.id)

    assert _by_side_number(sess.players) == {
        (HOME, 8): "Vidal",
        (HOME, 10): "Sosa",
        ("away", 8): "Mendes",
        ("away", 7): "Rocha",
    }


@pytest.mark.asyncio
async def test_merge_roster_overwrites_same_side_number(service):
    """Re-sending a team's photo overwrites the entry, no duplicate row."""
    sess, _ = await service.start_session(1, "Boca", "River", None)
    await service.merge_roster(sess, [ParsedPlayer(8, "Vidal", "CM", HOME)])
    sess = await service.storage.get_session(sess.id)

    # A corrected second photo: same (home, 8), better name/position.
    await service.merge_roster(sess, [ParsedPlayer(8, "Vidal Pérez", "DM", HOME)])
    sess = await service.storage.get_session(sess.id)

    players = list(sess.players)
    assert len(players) == 1
    assert players[0].name == "Vidal Pérez"
    assert players[0].position == "DM"


@pytest.mark.asyncio
async def test_merge_roster_appends_null_numbered_players(service):
    """Players with no jersey number are appended, never collapsed together."""
    sess, _ = await service.start_session(1, "Boca", "River", None)
    await service.merge_roster(sess, [ParsedPlayer(None, "García", "GK", HOME)])
    sess = await service.storage.get_session(sess.id)
    await service.merge_roster(sess, [ParsedPlayer(None, "Romero", "LB", HOME)])
    sess = await service.storage.get_session(sess.id)

    names = sorted(p.name for p in sess.players)
    assert names == ["García", "Romero"]


# ── manual lineup parser (Feature 2) ──────────────────────────────────────────
def test_parse_manual_lineup_two_sides_multiword_names():
    entries = _parse_manual_lineup("local: 10 Messi DC, 7 Di María; visitante: 5 Ramos")
    assert entries == [
        (HOME, 10, "Messi", "DC"),
        (HOME, 7, "Di María", None),
        ("away", 5, "Ramos", None),
    ]


def test_parse_manual_lineup_defaults_to_home():
    # No side prefix → home.
    assert _parse_manual_lineup("10 Messi, 7 Di María") == [
        (HOME, 10, "Messi", None),
        (HOME, 7, "Di María", None),
    ]


def test_parse_manual_lineup_optional_number_and_position():
    # Name only, name + position, number + name + position.
    assert _parse_manual_lineup("visitante: Ramos, Pérez CB, 9 Núñez ST") == [
        ("away", None, "Ramos", None),
        ("away", None, "Pérez", "CB"),
        ("away", 9, "Núñez", "ST"),
    ]


def test_parse_manual_lineup_empty_and_blank_chunks():
    assert _parse_manual_lineup("") == []
    assert _parse_manual_lineup("   ;  , ") == []
    # A single player (the "only watch one player" case).
    assert _parse_manual_lineup("local: 10 Messi") == [(HOME, 10, "Messi", None)]
