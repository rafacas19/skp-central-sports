"""Team-category derivation: the pure split of a team name into club + category.

No database — `categories.split_category` is a pure function, and these are the
cases the scout actually types (and the club names that must NOT be mistaken for
a category).
"""

import pytest

from scouting_bot.categories import derive_category, split_category


@pytest.mark.parametrize(
    "team, club, category",
    [
        # The example from the requirement, and the spellings that mean the same.
        ("Santa Fe U18", "Santa Fe", "Sub-18"),
        ("Santa Fe Sub-18", "Santa Fe", "Sub-18"),
        ("Santa Fe sub 18", "Santa Fe", "Sub-18"),
        ("Santa Fe SUB18", "Santa Fe", "Sub-18"),
        ("Santa Fe u-18", "Santa Fe", "Sub-18"),
        ("Santa Fe S18", "Santa Fe", "Sub-18"),
        # The category is not always last.
        ("Sub-18 Millonarios", "Millonarios", "Sub-18"),
        ("Millonarios FC U-18", "Millonarios FC", "Sub-18"),
        # Separators left behind are cleaned up.
        ("Santa Fe - Sub 18", "Santa Fe", "Sub-18"),
        ("Cali / Sub-20", "Cali", "Sub-20"),
        # Named categories, canonicalized.
        ("Cali Femenino", "Cali", "Femenino"),
        ("Deportivo Cali femenina", "Deportivo Cali", "Femenino"),
        ("América Reserva", "América", "Reserva"),
        ("Nacional Juvenil", "Nacional", "Juvenil"),
        ("Nacional Absoluta", "Nacional", "Profesional"),
        ("Nacional Profesional", "Nacional", "Profesional"),
        # Range bounds.
        ("Boca Sub-10", "Boca", "Sub-10"),
        ("Boca Sub-23", "Boca", "Sub-23"),
    ],
)
def test_splits_the_category_out_of_the_name(team, club, category):
    assert split_category(team) == (club, category)
    assert derive_category(team) == category


@pytest.mark.parametrize(
    "team",
    [
        "Millonarios",
        "Santa Fe",
        "Once Caldas",          # a number word, not a category
        "U de Chile",           # a bare "U" is the club, never a category
        "U. Católica",
        "Barcelona 1899",       # a founding year is not a category
        "Boca U9",              # below the age range
        "Boca U24",             # above the age range
        "Sub-18",               # nothing but a category → leave it alone
        "",
    ],
)
def test_leaves_names_without_a_category_untouched(team):
    assert split_category(team) == (team, None)


def test_none_team():
    assert split_category(None) == (None, None)


def test_is_idempotent():
    """The backfill can be re-run: a split name splits to itself."""
    club, category = split_category("Santa Fe U18")
    assert split_category(club) == (club, None)
    assert category == "Sub-18"
