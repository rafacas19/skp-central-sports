"""Domain taxonomies — the closed starter lists from the product outline.

These are intentionally small and centralized. Open question #1 in the product
outline flags that the skill taxonomy must be co-designed with real scouts; keep
this list as the single source of truth so refining it later touches one place.
"""

from __future__ import annotations

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
