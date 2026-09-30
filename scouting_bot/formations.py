"""Formation presets for the web lineup editor and the pitch view.

Each preset is eleven slots in a fixed order (goalkeeper first, then back to
front, left to right). A slot carries the canonical role it asks for — which is
what suggests the player's scoring profile — and where it sits on the pitch.

Coordinates are percentages of one team's half, seen from that team's own
goal: `x` 0 → 100 is left → right, `y` 0 is the goal line and 100 the halfway
line. The pitch view mirrors them for the away side.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    role: str  # a positions.ROLES role name
    x: int
    y: int


_GK = Slot("Portero", 50, 12)
_BACK_FOUR = (
    Slot("Lateral izquierdo", 12, 31),
    Slot("Defensa central", 37, 27),
    Slot("Defensa central", 63, 27),
    Slot("Lateral derecho", 88, 31),
)

FORMATIONS: dict[str, tuple[Slot, ...]] = {
    "4-3-3": (
        _GK, *_BACK_FOUR,
        Slot("Mediocentro", 24, 54),
        Slot("Pivote", 50, 46),
        Slot("Mediocentro", 76, 54),
        Slot("Extremo izquierdo", 15, 82),
        Slot("Delantero centro", 50, 88),
        Slot("Extremo derecho", 85, 82),
    ),
    "4-4-2": (
        _GK, *_BACK_FOUR,
        Slot("Extremo izquierdo", 12, 58),
        Slot("Mediocentro", 37, 52),
        Slot("Mediocentro", 63, 52),
        Slot("Extremo derecho", 88, 58),
        Slot("Delantero centro", 35, 86),
        Slot("Delantero centro", 65, 86),
    ),
    "4-2-3-1": (
        _GK, *_BACK_FOUR,
        Slot("Pivote", 35, 45),
        Slot("Pivote", 65, 45),
        Slot("Extremo izquierdo", 14, 68),
        Slot("Mediocentro ofensivo", 50, 66),
        Slot("Extremo derecho", 86, 68),
        Slot("Delantero centro", 50, 89),
    ),
    "3-5-2": (
        _GK,
        Slot("Defensa central", 25, 28),
        Slot("Defensa central", 50, 26),
        Slot("Defensa central", 75, 28),
        Slot("Lateral izquierdo", 8, 52),
        Slot("Mediocentro", 30, 56),
        Slot("Pivote", 50, 45),
        Slot("Mediocentro", 70, 56),
        Slot("Lateral derecho", 92, 52),
        Slot("Delantero centro", 35, 86),
        Slot("Delantero centro", 65, 86),
    ),
}

DEFAULT_FORMATION = "4-3-3"
# Substitutes a lineup form offers room for (more can be added on a second save).
BENCH_ROWS = 9


def get_formation(name: str | None) -> tuple[str, tuple[Slot, ...]]:
    """A known preset by name, falling back to the default."""
    if name in FORMATIONS:
        return name, FORMATIONS[name]
    return DEFAULT_FORMATION, FORMATIONS[DEFAULT_FORMATION]


def pitch_point(slot: Slot, away: bool) -> tuple[float, float]:
    """(left %, top %) of a slot on the full vertical pitch.

    The home side defends the bottom goal and the away side the top one, so
    both teams are read facing each other — the away side is mirrored in both
    axes, which keeps each team's left winger on its own left."""
    if away:
        return 100 - slot.x, slot.y / 2
    return slot.x, 100 - slot.y / 2
