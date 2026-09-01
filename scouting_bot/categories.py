"""Team categories (Spanish football) derived from the team's own name.

There is no team entity in this product — a team is the free text the scout
typed ("Santa Fe U18"). That string carries two facts at once: the club and the
age/competition category. This module splits them, so the club is what the
player's identity keys on and the category is stored beside it.

Splitting (rather than only tagging) is deliberate: prospect identity is keyed on
the normalized team (see storage.get_or_create_prospect), so "Santa Fe" and
"Santa Fe U18" must reduce to the same club or the same player becomes two
records.

The vocabulary is closed and conservative — a name that carries no recognizable
category is returned untouched with no category, which is the common case. Stdlib
only, pure functions.
"""

from __future__ import annotations

import re
import unicodedata

# Age categories run Sub-10 to Sub-23; anything outside that is a squad number,
# a founding year or a street address, not a category.
AGE_MIN, AGE_MAX = 10, 23

# Named (non-numeric) categories, canonical label → the words that mean it.
# "Absoluta" is the senior squad, i.e. the same tier as "Profesional".
NAMED_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Juvenil": ("juvenil", "juveniles"),
    "Reserva": ("reserva", "reservas"),
    "Femenino": ("femenino", "femenina"),
    "Profesional": ("profesional", "absoluta", "absoluto"),
}

# The numeric form: "Sub-18", "sub 18", "SUB18", "U18", "u-18", "S18". The digits
# are required — a bare "U" is a club name ("U de Chile", "U. Católica"), never a
# category, and the same goes for a lone "S".
_AGE_RE = re.compile(r"(?<![0-9a-záéíóúñ])(?:sub|u|s)\s*-?\s*([0-9]{1,2})\b", re.IGNORECASE)

# Separators left dangling once a category is cut out of the middle or the end
# of a name ("Santa Fe - Sub 18", "Cali / Femenino").
_TRIM = " \t-–—·/,|"


def _fold(text: str) -> str:
    """Lowercase and strip accents, for matching only (never for storage)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _named_match(name: str) -> tuple[int, int, str] | None:
    """The first named category in `name` as (start, end, canonical), or None.

    Matched on whole words over an accent-folded copy whose character offsets
    line up with the original (folding only drops combining marks), so the span
    can be cut out of the untouched string.
    """
    folded = _fold(name)
    best: tuple[int, int, str] | None = None
    for canonical, words in NAMED_CATEGORIES.items():
        for word in words:
            for m in re.finditer(rf"\b{word}\b", folded):
                if best is None or m.start() < best[0]:
                    best = (m.start(), m.end(), canonical)
    return best


def split_category(team: str | None) -> tuple[str | None, str | None]:
    """Split a team name into (club, category).

    "Santa Fe U18"      → ("Santa Fe", "Sub-18")
    "Millonarios"       → ("Millonarios", None)
    "Cali Femenino"     → ("Cali", "Femenino")
    "U de Chile"        → ("U de Chile", None)   ← a bare "U" is the club

    The club keeps the scout's own spelling, minus the category token. A name
    that is *only* a category ("Sub-18") is returned unchanged with no category:
    stripping it would leave no team at all. Idempotent — a name already split
    yields itself and None, which is what makes the backfill safe to re-run.
    """
    if not team or not team.strip():
        return (team, None)

    name = team.strip()
    age = _AGE_RE.search(name)
    if age is not None and AGE_MIN <= int(age.group(1)) <= AGE_MAX:
        span, category = (age.start(), age.end()), f"Sub-{int(age.group(1))}"
    else:
        named = _named_match(name)
        if named is None:
            return (name, None)
        span, category = (named[0], named[1]), named[2]

    club = (name[: span[0]].strip(_TRIM) + " " + name[span[1] :].strip(_TRIM)).strip(_TRIM)
    club = re.sub(r"\s{2,}", " ", club)
    if not club:
        return (name, None)  # the name was nothing but a category
    return (club, category)


def derive_category(team: str | None) -> str | None:
    """Just the category of a team name, or None. `split_category` for both."""
    return split_category(team)[1]
