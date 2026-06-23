"""Match CSV (new columns) + cross-match player report (Phase 4)."""

import csv
import io

import pytest

from scouting_bot.report import (
    _CSV_COLUMNS,
    build_csv,
    build_player_report,
    build_summary,
)


async def _capture(service, sess, text, source="text"):
    for r in await service.capture_notes(sess, text, source=source):
        if r.needs_team_choice:
            await service.resolve_team_choice(sess, r.classified, r.team_candidates[0])


@pytest.mark.asyncio
async def test_csv_columns_and_opponent(service):
    await service.storage.set_scout_name(1, "Rafa")
    sess, _ = await service.start_session(1, "Millonarios", "América", None)
    await _capture(service, sess, "Castro de América buen pase valoración 8")
    await _capture(service, sess, "América presiona alto")  # team note
    ended = await service.end_session(sess)

    raw = build_csv(ended)
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == _CSV_COLUMNS
    data = rows[1:]

    col = {name: i for i, name in enumerate(_CSV_COLUMNS)}
    castro = next(r for r in data if "Castro" in r[col["Observation"]])
    assert castro[col["Match"]] == "Millonarios vs América"
    assert castro[col["Team"]] == "América"
    assert castro[col["Opponent"]] == "Millonarios"  # derived
    assert castro[col["Player name"]] == "Castro"
    assert castro[col["Manual rating"]] == "8"
    assert castro[col["Scout"]] == "Rafa"
    assert castro[col["Source"]] == "text"


@pytest.mark.asyncio
async def test_csv_handles_empty_session(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    ended = await service.end_session(sess)
    raw = build_csv(ended)
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == _CSV_COLUMNS
    assert len(rows) == 1  # header only


@pytest.mark.asyncio
async def test_player_report_spans_matches_with_summary(service):
    chat = 3
    m1, _ = await service.start_session(chat, "América", "Nacional", None)
    await _capture(service, m1, "Castro de América buen pase")
    await service.end_session(m1)
    m2, _ = await service.start_session(chat, "Millonarios", "América", None)
    await _capture(service, m2, "Castro de América gana los duelos")
    await service.end_session(m2)

    result = await service.player_report(chat, "Castro")
    assert not isinstance(result, list)
    prospect, observations, summary = result
    assert len(observations) == 2
    report = build_player_report(prospect, observations, summary)
    assert "Castro" in report
    assert "Historial" in report and "Resumen" in report
    assert "buen pase" in report and "gana los duelos" in report
    # Both matches appear in the history.
    assert "América vs Nacional" in report and "Millonarios vs América" in report


@pytest.mark.asyncio
async def test_summary_groups_by_player(service):
    sess, _ = await service.start_session(1, "A", "B", None)
    await _capture(service, sess, "Castro de A buen pase")
    await _capture(service, sess, "Castro de A buen control")
    ended = await service.end_session(sess)
    summary = build_summary(ended)
    assert "Castro" in summary
    assert "2 obs" in summary  # two observations rolled up for Castro
