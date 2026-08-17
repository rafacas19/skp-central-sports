"""Canonical football positions (Spanish) and a mapper over free text.

The bot stores whatever the scout said ("delantero", "volante de marca"), which
is faithful but ungroupable. This module adds the two-level taxonomy every
scouting product uses — a broad *category* (Portero / Defensa / Centrocampista /
Delantero) plus a specific *role* — and maps existing free text onto it.

The mapping is read-only: stored text is never rewritten, so the scout's own
words survive. The dashboard edit form writes canonical role names going
forward, at which point text and taxonomy agree. When text maps to no role but
to a category ("lateral" — which side?), the category alone is used; filtering
still works and the form lets the scout pick the exact role.

Role names and their order follow the Spanish market standard (transfermarkt.es).
Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import normalize_name

# Broad categories, in pitch order (goal outwards). This is the grouping used to
# rank players within their position, as every scouting tool does.
CATEGORIES = ("Portero", "Defensa", "Centrocampista", "Delantero")


@dataclass(frozen=True)
class Position:
    """A canonical role: display name, its category, and a badge abbreviation."""

    role: str
    category: str
    abbr: str


# The canonical roles offered in the edit form, in pitch order.
ROLES: tuple[Position, ...] = (
    Position("Portero", "Portero", "POR"),
    Position("Defensa central", "Defensa", "DFC"),
    Position("Lateral izquierdo", "Defensa", "LI"),
    Position("Lateral derecho", "Defensa", "LD"),
    Position("Pivote", "Centrocampista", "PIV"),
    Position("Mediocentro", "Centrocampista", "MC"),
    Position("Mediocentro ofensivo", "Centrocampista", "MCO"),
    Position("Mediapunta", "Delantero", "MP"),
    Position("Extremo izquierdo", "Delantero", "EI"),
    Position("Extremo derecho", "Delantero", "ED"),
    Position("Delantero centro", "Delantero", "DC"),
)

_BY_ROLE = {normalize_name(p.role): p for p in ROLES}
_BY_ABBR = {p.abbr.lower(): p for p in ROLES}

# Free-text phrases that identify a specific role. Matched as substrings, so
# Colombian/Spanish variants land on the right role ("volante de marca" is a
# holding midfielder, "puntero" a winger). Longest phrase wins, which is what
# keeps "media punta" from being read as "punta".
_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Portero": ("portero", "portera", "arquero", "arquera", "guardameta", "golero"),
    "Defensa central": (
        "defensa central", "central", "zaguero", "zaguera", "libero", "stopper",
    ),
    "Lateral izquierdo": (
        "lateral izquierdo", "lateral izq", "lateral zurdo", "carrilero izquierdo",
    ),
    "Lateral derecho": (
        "lateral derecho", "lateral der", "carrilero derecho",
    ),
    "Pivote": (
        "pivote", "volante de marca", "volante de contencion", "volante defensivo",
        "mediocentro defensivo", "medio defensivo", "contencion", "recuperador",
    ),
    "Mediocentro": (
        "mediocentro", "medio centro", "volante central", "volante mixto",
        "interior",
    ),
    "Mediocentro ofensivo": (
        "mediocentro ofensivo", "medio ofensivo", "volante ofensivo",
        "volante de creacion", "creador", "enganche", "armador",
    ),
    "Mediapunta": (
        "mediapunta", "media punta", "segundo delantero", "segunda punta",
    ),
    "Extremo izquierdo": (
        "extremo izquierdo", "extremo izq", "puntero izquierdo", "extremo zurdo",
    ),
    "Extremo derecho": (
        "extremo derecho", "extremo der", "puntero derecho",
    ),
    "Delantero centro": (
        "delantero centro", "centro delantero", "delantero de area", "ariete",
        "nueve", "goleador",
    ),
}

# Phrases that pin down only the category — the scout named a line, not a role
# ("lateral" without a side, "volante" without a job).
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Portero": ("arco",),
    "Defensa": ("defensa", "defensor", "defensora", "lateral", "carrilero", "marcador"),
    "Centrocampista": (
        "centrocampista", "mediocampista", "mediocampo", "medio campo", "volante",
        "medio",
    ),
    "Delantero": (
        "delantero", "delantera", "atacante", "extremo", "puntero", "punta",
        "artillero",
    ),
}

# (phrase, Position) pairs, longest phrase first, so the most specific mention in
# a free-text position wins regardless of table order.
_ROLE_PHRASES: tuple[tuple[str, Position], ...] = tuple(
    sorted(
        (
            (phrase, _BY_ROLE[normalize_name(role)])
            for role, phrases in _ROLE_KEYWORDS.items()
            for phrase in phrases
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)

_CATEGORY_PHRASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            (phrase, category)
            for category, phrases in _CATEGORY_KEYWORDS.items()
            for phrase in phrases
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)


def canonical_position(text: str | None) -> Position | None:
    """The canonical role a free-text position names, or None if only a line
    (or nothing) can be read from it.

    Exact role names and abbreviations match first; otherwise the longest
    recognised phrase inside the text decides."""
    if not text:
        return None
    norm = normalize_name(text)
    if not norm:
        return None
    if norm in _BY_ROLE:
        return _BY_ROLE[norm]
    if norm in _BY_ABBR:
        return _BY_ABBR[norm]
    for phrase, position in _ROLE_PHRASES:
        if phrase in norm:
            return position
    return None


def position_category(text: str | None) -> str | None:
    """The broad line a free-text position belongs to, or None if unreadable.

    Falls back to category-only phrases when no specific role is identifiable,
    so "lateral" still groups under Defensa."""
    role = canonical_position(text)
    if role is not None:
        return role.category
    if not text:
        return None
    norm = normalize_name(text)
    for phrase, category in _CATEGORY_PHRASES:
        if phrase in norm:
            return category
    return None


def position_abbr(text: str | None) -> str | None:
    """Badge abbreviation for a free-text position (None when unmappable)."""
    role = canonical_position(text)
    return role.abbr if role else None


def category_index(category: str | None) -> int:
    """Sort key placing categories in pitch order, unknowns last."""
    return CATEGORIES.index(category) if category in CATEGORIES else len(CATEGORIES)
