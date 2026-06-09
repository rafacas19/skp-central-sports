"""Domain taxonomies — the closed starter lists from the product outline.

These are intentionally small and centralized. Open question #1 in the product
outline flags that the skill taxonomy must be co-designed with real scouts; keep
this list as the single source of truth so refining it later touches one place.
"""

from __future__ import annotations

import difflib
import unicodedata

# Skill categories an observation can be tagged with.
SKILL_CATEGORIES: tuple[str, ...] = (
    "pace",
    "passing",
    "first_touch",
    "aerial_duels",
    "defending",
    "finishing",
    "decision_making",
    "work_rate",
    "positioning",
    "dribbling",
    "physicality",
    "other",
)

# Spanish display labels for the skill categories.
# The internal keys above stay in English (stored in the DB and used in the AI
# prompt); this map is presentation-only, so reports read naturally in Spanish.
SKILL_LABELS_ES: dict[str, str] = {
    "pace": "velocidad",
    "passing": "pase",
    "first_touch": "control / primer toque",
    "aerial_duels": "juego aéreo",
    "defending": "defensa",
    "finishing": "definición",
    "decision_making": "toma de decisiones",
    "work_rate": "intensidad / trabajo",
    "positioning": "posicionamiento",
    "dribbling": "regate",
    "physicality": "físico",
    "other": "otro",
}


def skill_label(key: str | None) -> str:
    """Spanish display label for a skill category key (presentation layer)."""
    if not key:
        return SKILL_LABELS_ES["other"]
    return SKILL_LABELS_ES.get(key, key)


# Sentiment values.
SENTIMENT_POSITIVE = "positive"
SENTIMENT_NEGATIVE = "negative"
SENTIMENTS: tuple[str, ...] = (SENTIMENT_POSITIVE, SENTIMENT_NEGATIVE)


def normalize_skill(raw: str | None) -> str:
    """Map a free-form skill string onto the closed taxonomy."""
    if not raw:
        return "other"
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return key if key in SKILL_CATEGORIES else "other"


def normalize_sentiment(raw: str | None) -> str:
    """Map a free-form sentiment string onto positive/negative (default negative-safe)."""
    if not raw:
        return SENTIMENT_NEGATIVE
    key = raw.strip().lower()
    if key.startswith("pos") or key in {"good", "+", "great"}:
        return SENTIMENT_POSITIVE
    return SENTIMENT_NEGATIVE


# ── Player-name matching ──────────────────────────────────────────────────────
# Scouts refer to players by name far more than by number, and often only by
# surname, without accents, or with a small transcription typo ("perez" for
# "Pérez", "mendez" for "Mendes"). These helpers make name matching forgiving in
# both the deterministic matcher (service._candidates) and the mock AI provider,
# keeping the two paths in lockstep. Stdlib only — no extra dependencies.

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
