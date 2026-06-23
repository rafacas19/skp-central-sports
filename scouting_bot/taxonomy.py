"""Player-name matching helpers.

Scouts refer to players by name far more than by number, and often only by
surname, without accents, or with a small transcription typo ("perez" for
"Pérez", "mendez" for "Mendes"). These helpers make name matching forgiving for
prospect identity keys and the merge duplicate-detection. Stdlib only.
"""

from __future__ import annotations

import difflib
import unicodedata

# Similarity above which two name tokens are treated as the same (typo tolerance).
# 0.80 catches a single-character slip in a typical surname ("mendez" vs "mendes"
# ≈ 0.83, "perez" vs "peres" = 0.80) while staying well above the similarity of
# unrelated surnames.
NAME_MATCH_RATIO = 0.80


def normalize_name(s: str) -> str:
    """Lowercase and strip accents/diacritics for accent-insensitive comparison."""
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower().strip()


def name_matches(ref_name: str | None, player_name: str) -> bool:
    """Does a scout's free-form name reference plausibly mean this player?

    True on: full-name equality, surname-only / any-token containment
    (``messi`` → ``lionel messi``), and small typos (``mendez`` → ``mendes``),
    all accent-insensitively. Conservative enough (ratio ≥ NAME_MATCH_RATIO) to
    avoid matching unrelated surnames.
    """
    if not ref_name:
        return False
    ref = normalize_name(ref_name)
    full = normalize_name(player_name)
    if not ref or not full:
        return False
    tokens = full.split()
    if ref == full or ref in tokens:
        return True
    if difflib.SequenceMatcher(None, ref, full).ratio() >= NAME_MATCH_RATIO:
        return True
    return any(
        difflib.SequenceMatcher(None, ref, t).ratio() >= NAME_MATCH_RATIO
        for t in tokens
    )
