import csv
import io

import pytest

from scouting_bot.report import (
    _CSV_COLUMNS,
    build_csv,
    build_markdown,
    build_pdf,
    build_summary,
)


@pytest.mark.asyncio
async def test_full_match_report(service):
    # Full happy-path flow end-to-end with the mock AI.
    sess, _ = await service.start_session(1, "Boca", "River", "Liga, round 12")
    parsed = await service.parse_and_stage_roster(b"fake-image", "image/jpeg")
    await service.save_roster(sess, parsed)
    await service.confirm_roster(sess)
    sess = await service.storage.get_session(sess.id)

    # Flag a target.
    target = next(p for p in sess.players if p.name == "Sosa")
    await service.set_target(target, True)
    sess = await service.storage.get_session(sess.id)

    notes = [
        "Sosa great vision and passing",
        "Sosa brilliant first touch",
        "Núñez clinical finish",
        "Romero too slow on the turn",
        "the team is pressing high leaving space behind",
    ]
    for n in notes:
        for result in await service.capture_notes(sess, n):
            if result.needs_disambiguation:
                await service.resolve_disambiguation(
                    sess, result.classified, result.candidates[0]
                )

    ended = await service.end_session(sess)

    summary = build_summary(ended)
    assert "Boca" in summary and "River" in summary
    assert "Jugadores objetivo" in summary
    assert "Sosa" in summary

    md = build_markdown(ended)
    assert "# Informe del partido" in md
    assert "Sosa" in md
    assert "Notas de equipo" in md
    assert "pressing high" in md
    # Sosa got 2 positive notes.
    assert "2 positivas" in md or "👍 2" in md

    # PDF renders to valid, non-trivial PDF bytes.
    pdf = build_pdf(ended)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


@pytest.mark.asyncio
async def test_report_handles_empty_session(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    ended = await service.end_session(sess)
    # Should not raise even with no roster / no notes.
    assert "A" in build_summary(ended)
    assert "# Informe del partido" in build_markdown(ended)
    # PDF generation must not raise on an empty session either.
    pdf = build_pdf(ended)
    assert pdf.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_csv_report(service):
    sess, _ = await service.start_session(1, "Boca", "River", "Liga, round 12")
    parsed = await service.parse_and_stage_roster(b"fake-image", "image/jpeg")
    await service.save_roster(sess, parsed)
    await service.confirm_roster(sess)
    sess = await service.storage.get_session(sess.id)

    # Two player notes (unique names) + one team note.
    for note in ("Sosa great vision", "Núñez clinical finish"):
        for r in await service.capture_notes(sess, note):
            assert not r.needs_disambiguation
    await service.capture_notes(sess, "the team is pressing high leaving space")
    ended = await service.end_session(sess)

    raw = build_csv(ended)
    assert isinstance(raw, bytes)
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))

    assert rows[0] == _CSV_COLUMNS
    data = rows[1:]
    assert len(data) == 3  # 2 player + 1 team observation

    by_quote = {row[_CSV_COLUMNS.index("raw_quote")]: row for row in data}
    sosa = by_quote["Sosa great vision"]
    assert sosa[_CSV_COLUMNS.index("match")] == "Boca vs River"
    assert sosa[_CSV_COLUMNS.index("player_name")] == "Sosa"
    assert sosa[_CSV_COLUMNS.index("player_number")] == "10"
    assert sosa[_CSV_COLUMNS.index("sentiment")] == "positive"

    team = by_quote["the team is pressing high leaving space"]
    assert team[_CSV_COLUMNS.index("player_name")] == ""
    assert team[_CSV_COLUMNS.index("player_number")] == ""
