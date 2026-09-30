"""The client's position profiles, transcribed from their workbook."""

from decimal import Decimal
from pathlib import Path

import pytest

from scouting_bot import profiles
from scouting_bot.profiles import (
    PROFILES,
    average,
    clean_scores,
    default_profile,
    format_average,
    get_profile,
    match_rating,
)

WORKBOOK = Path(__file__).resolve().parent.parent / "feedback" / "Perfiles_Scout.xlsx"


def test_six_profiles_with_the_clients_criterion_counts():
    counts = {p.name: len(p.criteria) for p in PROFILES}
    assert counts == {
        "Defensa Central": 24,
        "Lateral": 24,
        "Medio Centro": 22,
        "Interior": 23,
        "Extremo": 22,
        "Centro Delantero": 22,
    }
    for p in PROFILES:
        assert [s.number for s in p.sections] == [1, 2, 3, 4, 5]
        assert len(p.codes) == len(p.criteria)  # codes unique within a profile


def test_section_titles_read_as_prose():
    lateral = get_profile("lateral")
    assert [s.title for s in lateral.sections] == [
        "Técnica",
        "Táctica defensiva",
        "Táctica ofensiva",
        "Condicional",
        "Mental (cognitivo y volitivo)",
    ]
    assert (lateral.build, lateral.height) == ("Atlético", "Mediano")


@pytest.mark.skipif(not WORKBOOK.exists(), reason="client workbook not present locally")
def test_profiles_match_the_clients_workbook_row_for_row():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(WORKBOOK)
    assert [ws.title for ws in wb.worksheets] == [p.name for p in PROFILES]
    for ws, profile in zip(wb.worksheets, PROFILES):
        rows = []
        build = []
        for code, name, desc in ws.iter_rows(min_row=5, max_col=3, values_only=True):
            if code and name:
                rows.append((str(code), name.strip(), " ".join((desc or "").split())))
            elif name and not code:
                build.append(name.strip())
        expected = [
            (c.code, c.name, c.description) for c in profile.criteria
        ]
        fixed = [(c, "Concentración" if n == "Concetración" else n, d) for c, n, d in rows]
        assert fixed == expected, profile.name
        assert build == [profile.build, profile.height]


@pytest.mark.parametrize(
    "position, key",
    [
        ("Defensa central", "defensa_central"),
        ("Lateral izquierdo", "lateral"),
        ("Lateral derecho", "lateral"),
        ("Pivote", "medio_centro"),
        ("Mediocentro", "medio_centro"),
        ("Mediocentro ofensivo", "interior"),
        ("Mediapunta", "interior"),
        ("Extremo izquierdo", "extremo"),
        ("Extremo derecho", "extremo"),
        ("Delantero centro", "centro_delantero"),
        ("volante de marca", "medio_centro"),
    ],
)
def test_default_profile_follows_the_position(position, key):
    assert default_profile(position).key == key


@pytest.mark.parametrize("position", ["Portero", "arquero", "lateral", None, "", "utilero"])
def test_no_profile_for_goalkeepers_or_vague_positions(position):
    assert default_profile(position) is None


def test_clean_scores_keeps_only_valid_whole_scores_of_the_profile():
    lateral = get_profile("lateral")
    raw = {"1.1": "4", "1.2": "7", "1.3": "", "1.4": "x", "9.9": "3", "2.1": 0, "2.2": 5}
    assert clean_scores(lateral, raw) == {"1.1": 4, "2.2": 5}


def test_match_rating_keeps_two_decimals_so_the_decision_does_not_flip():
    # 83/24 — the client's example. 3.5 would round to a 4 (Muy interesante).
    scores = {str(i): v for i, v in enumerate([4] * 13 + [3] * 9 + [2] * 2)}
    assert match_rating(scores) == 3.46
    assert match_rating({}) is None


def test_report_averages_round_half_up_with_a_comma():
    assert format_average(average([4, 3, 3, 4])) == "3,5"
    assert format_average(average([4, 3, 4, 4, 4, 4, 3, 3, 4])) == "3,7"
    assert format_average(average([2, 3, 3, 4, 3, 4])) == "3,2"
    assert format_average(average([4, 2, 4])) == "3,3"
    assert average([3, 3.5]) == Decimal("3.3")  # 3.25 → 3.3, not banker's 3.2
    assert format_average(average([])) == "—"
    assert format_average(average([None, None])) == "—"


def test_role_mapping_only_names_real_profiles():
    for key in profiles.ROLE_PROFILES.values():
        assert get_profile(key) is not None
