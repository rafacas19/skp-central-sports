"""Position taxonomy: mapping the scout's free text onto canonical roles.

Pure functions, no database — these run even without a test Postgres.
"""

from datetime import date

import pytest

from scouting_bot.models import current_age
from scouting_bot.positions import (
    CATEGORIES,
    ROLES,
    canonical_position,
    category_index,
    position_abbr,
    position_category,
)

@pytest.mark.parametrize(
    "text,role",
    [
        # Exact canonical names and abbreviations.
        ("Delantero centro", "Delantero centro"),
        ("delantero centro", "Delantero centro"),
        ("DC", "Delantero centro"),
        ("por", "Portero"),
        # Colombian / Spanish everyday wording.
        ("arquero", "Portero"),
        ("zaguero central", "Defensa central"),
        ("lateral izquierdo", "Lateral izquierdo"),
        ("volante de marca", "Pivote"),
        ("volante de contención", "Pivote"),
        ("volante central", "Mediocentro"),
        ("volante ofensivo", "Mediocentro ofensivo"),
        ("enganche", "Mediocentro ofensivo"),
        ("media punta", "Mediapunta"),
        ("mediapunta", "Mediapunta"),
        ("puntero derecho", "Extremo derecho"),
        ("ariete", "Delantero centro"),
        # Accents and casing are irrelevant (same normalization as names).
        ("DELANTERO DE ÁREA", "Delantero centro"),
    ],
)
def test_canonical_roles(text, role):
    position = canonical_position(text)
    assert position is not None, text
    assert position.role == role


@pytest.mark.parametrize(
    "text,category",
    [
        # A line without a role: no side, no job — still groupable.
        ("lateral", "Defensa"),
        ("volante", "Centrocampista"),
        ("defensa", "Defensa"),
        ("mediocampista", "Centrocampista"),
        ("Delantero", "Delantero"),
        ("atacante", "Delantero"),
        # Roles imply their category.
        ("arquero", "Portero"),
        ("volante de marca", "Centrocampista"),
        ("extremo izquierdo", "Delantero"),
    ],
)
def test_categories(text, category):
    assert position_category(text) == category


def test_specific_wording_beats_generic():
    """The longest recognised phrase wins, so a qualified position is never
    flattened into its generic one."""
    assert canonical_position("media punta").role == "Mediapunta"
    assert canonical_position("volante de marca").role == "Pivote"
    assert canonical_position("lateral izquierdo").role == "Lateral izquierdo"


def test_unmappable_text_is_not_guessed():
    for text in (None, "", "   ", "juega bien", "9"):
        assert canonical_position(text) is None
        assert position_category(text) is None
        assert position_abbr(text) is None


def test_every_role_has_a_known_category_and_unique_abbr():
    assert {p.category for p in ROLES} <= set(CATEGORIES)
    assert len({p.abbr for p in ROLES}) == len(ROLES)
    # Every canonical role round-trips through the mapper.
    for p in ROLES:
        assert canonical_position(p.role) == p
        assert position_abbr(p.role) == p.abbr


def test_category_index_orders_by_pitch_position():
    order = [category_index(c) for c in CATEGORIES]
    assert order == sorted(order)
    assert category_index(None) == len(CATEGORIES)  # unknowns last


# ── Age derivation ───────────────────────────────────────────────────────
def test_age_prefers_birth_year():
    today = date(2026, 8, 16)
    assert current_age(2008, None, today) == 18
    # A stated age is superseded by the birth year (it goes stale, the year does not).
    assert current_age(2008, 15, today) == 18
    # No birth year: fall back to whatever the scout said.
    assert current_age(None, 17, today) == 17
    assert current_age(None, None, today) is None
    # Implausible years are ignored rather than shown.
    assert current_age(1800, 20, today) == 20
    assert current_age(2030, None, today) is None
