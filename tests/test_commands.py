"""Phase 3: scout name, team notes, ratings (incl. inline), /foto, metadata."""

import pytest

from scouting_bot.models import decision_for_rating
from scouting_bot.service import extract_inline_rating


# ── rating → decision mapping (pure) ──────────────────────────────────────────
def test_decision_for_rating():
    assert decision_for_rating(1) == "A descartar"
    assert decision_for_rating(2) == "A seguir"
    assert decision_for_rating(3) == "Interesante"
    assert decision_for_rating(4) == "Muy interesante"
    assert decision_for_rating(5) == "A firmar"
    assert decision_for_rating(None) is None
    # Decimals round to the nearest whole bucket; out-of-range clamps.
    assert decision_for_rating(4.4) == "Muy interesante"
    assert decision_for_rating(0) == "A descartar"
    assert decision_for_rating(6) == "A firmar"


# ── inline rating parsing (pure) ──────────────────────────────────────────────
def test_extract_inline_rating():
    assert extract_inline_rating("Castro valoración 4") == ("Castro", 4.0)
    assert extract_inline_rating("Castro América valoración 4.5")[1] == 4.5
    assert extract_inline_rating("rating 3")[1] == 3.0
    # No rating → text untouched.
    assert extract_inline_rating("buen primer toque") == ("buen primer toque", None)
    # Out of range (1–5) → ignored.
    assert extract_inline_rating("valoración 8")[1] is None
    assert extract_inline_rating("valoración 0")[1] is None


# ── service: team notes, ratings, photo ───────────────────────────────────────
@pytest.mark.asyncio
async def test_add_team_note(service):
    sess, _ = await service.start_session(1, "Millonarios", "América", None)
    obs = await service.add_team_note(sess, "juegan 4-2-3-1", "América")
    assert obs.is_team_note
    assert obs.prospect_id is None
    assert obs.team == "América" and obs.side == "away"


@pytest.mark.asyncio
async def test_rate_by_name(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    await service.capture_notes(sess, "Castro de A buen pase")
    result = await service.rate_by_name(sess.agent_chat_id, "Castro", 4)
    assert not isinstance(result, list)
    assert result.latest_rating == 4
    # Rating auto-derives the decision (1–5 mapping).
    assert result.decision_status == "Muy interesante"


@pytest.mark.asyncio
async def test_inline_rating_sets_observation_and_prospect(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    r = (await service.capture_notes(sess, "Castro de A buen pase valoración 4"))[0]
    assert r.observation.rating == 4.0
    assert r.prospect.latest_rating == 4.0
    assert r.prospect.decision_status == "Muy interesante"  # auto-decision
    # The rating phrase is stripped from the stored quote.
    assert "valoración" not in r.observation.raw_quote.lower()


@pytest.mark.asyncio
async def test_attach_photo_and_capture(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    prospect = await service.attach_photo(sess, "A", "tg-file-123")
    assert prospect.is_temporary
    assert prospect.photo_file_id == "tg-file-123"
    obs = await service.capture_to_prospect(sess, "muy alto, buen juego aéreo", prospect)
    assert obs.prospect_id == prospect.id


@pytest.mark.asyncio
async def test_start_session_copies_scout_name(service):
    chat = 5
    await service.storage.set_scout_name(chat, "Rafa")
    sess, _ = await service.start_session(chat, "A", "B", None)
    assert sess.scout_name == "Rafa"


# ── decisions / edit / merge (Phase 5) ────────────────────────────────────────
@pytest.mark.asyncio
async def test_set_decision_by_name(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    await service.capture_notes(sess, "Castro de A buen pase")
    result = await service.set_decision_by_name(sess.agent_chat_id, "Castro", "Avanzar")
    assert not isinstance(result, list)
    assert result.decision_status == "Avanzar"


@pytest.mark.asyncio
async def test_edit_prospect_fields(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    await service.capture_notes(sess, "Castro de A buen pase")
    result = await service.edit_prospect(
        sess.agent_chat_id, "Castro", {"age": 22, "height_cm": 180, "position": "DM"}
    )
    assert not isinstance(result, list)
    assert result.age == 22 and result.height_cm == 180 and result.position == "DM"


@pytest.mark.asyncio
async def test_edit_names_a_temporary_prospect(service):
    """Editing the name of a /foto temp profile clears the temporary flag."""
    sess, _ = await service.start_session(1, "A", "B", None)
    temp = await service.attach_photo(sess, "A", "file-1")
    assert temp.is_temporary
    result = await service.edit_prospect(
        sess.agent_chat_id, temp.name or "", {"name": "Castro", "team": "A"}
    )
    assert not isinstance(result, list)
    assert result.name == "Castro" and not result.is_temporary


@pytest.mark.asyncio
async def test_detect_duplicate_and_merge(service):
    chat = 9
    m1, _ = await service.start_session(chat, "A", "B", None)
    await service.capture_notes(m1, "Castro de A buen pase")  # creates "Castro"
    # A near-duplicate name on the same team.
    p2 = await service.storage.get_or_create_prospect(chat, "Castru", "A")
    dup = await service.detect_duplicate(chat, "Castru", "A", exclude_id=p2.id)
    assert dup is not None and dup.name == "Castro"

    await service.merge(dup.id, p2.id)  # keep Castro, drop Castru
    assert await service.storage.get_prospect(p2.id) is None


# ── /editar parser (pure) ─────────────────────────────────────────────────────
def test_parse_edit():
    from scouting_bot.bot import _parse_edit

    name, fields = _parse_edit("Castro edad=22 altura=180 posicion=DM")
    assert name == "Castro"
    assert fields == {"age": 22, "height_cm": 180, "position": "DM"}

    name2, fields2 = _parse_edit("Jugador desconocido nombre=Daniel Castro equipo=América")
    assert fields2["name"] == "Daniel Castro" and fields2["team"] == "América"


# ── /nuevo metadata parsing (pure) ────────────────────────────────────────────
def test_parse_match_metadata():
    from scouting_bot.bot import _parse_match_metadata

    home, away, label, meta = _parse_match_metadata(
        "Millonarios vs América | competición=Liga | fecha=2026-06-20 | sede=El Campín"
    )
    assert home == "Millonarios" and away == "América"
    assert meta["competition"] == "Liga"
    assert meta["location"] == "El Campín"
    assert meta["match_date"].year == 2026 and meta["match_date"].month == 6
    # A plain segment (no '=') becomes the label.
    _, _, label2, _ = _parse_match_metadata("A vs B | jornada 12")
    assert label2 == "jornada 12"
