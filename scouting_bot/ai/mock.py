"""Deterministic mock AI provider.

No API keys required. It is intentionally *real enough* to exercise every branch
of the bot flow:

  - parse_lineup: returns a fixed two-team roster (so the confirm step is testable)
  - transcribe_voice: returns a deterministic placeholder transcript
  - classify_notes: actually parses the text — splits a message that qualifies
    several players, then per fragment extracts jersey number / name / position
    references, detects sentiment from keywords, maps a skill category, and
    lowers confidence when the player reference is ambiguous (so the "ask only
    when unsure" path triggers for real).

Swap to RealAIProvider (Claude + Whisper) by setting USE_MOCK_AI=false.
"""

from __future__ import annotations

import re

from ..taxonomy import (
    SENTIMENT_NEGATIVE,
    SENTIMENT_POSITIVE,
    name_matches,
    normalize_name,
    normalize_skill,
)
from .base import AIProvider, ClassifiedNote, ParsedPlayer, PlayerMatch

# A fixed demo roster the mock "reads" from any lineup image.
_DEMO_ROSTER: list[ParsedPlayer] = [
    ParsedPlayer(1, "García", "GK", "home"),
    ParsedPlayer(4, "Pérez", "CB", "home"),
    ParsedPlayer(3, "Romero", "LB", "home"),
    ParsedPlayer(8, "Vidal", "CM", "home"),
    ParsedPlayer(10, "Sosa", "AM", "home"),
    ParsedPlayer(9, "Núñez", "ST", "home"),
    ParsedPlayer(1, "Silva", "GK", "away"),
    ParsedPlayer(5, "Costa", "CB", "away"),
    ParsedPlayer(2, "Lima", "RB", "away"),
    ParsedPlayer(8, "Mendes", "CM", "away"),
    ParsedPlayer(7, "Rocha", "RW", "away"),
    ParsedPlayer(11, "Alves", "ST", "away"),
]

_POSITIVE_WORDS = {
    "great", "good", "excellent", "strong", "brilliant", "sharp", "clinical",
    "composed", "quick", "fast", "beat", "won", "dominant", "smart", "nice",
    "buen", "buena", "bueno", "excelente", "rápido", "ganó", "gran",
}
_NEGATIVE_WORDS = {
    "slow", "weak", "poor", "lost", "missed", "bad", "late", "sloppy", "lazy",
    "caught", "beaten", "wasteful", "careless", "soft", "lento", "lenta",
    "malo", "mala", "perdió", "falló", "flojo",
}

# Keyword → skill-category hints.
_SKILL_HINTS: dict[str, str] = {
    "touch": "first_touch", "control": "first_touch",
    "pass": "passing", "passing": "passing", "ball": "passing",
    "pace": "pace", "fast": "pace", "quick": "pace", "slow": "pace", "speed": "pace",
    "head": "aerial_duels", "aerial": "aerial_duels", "header": "aerial_duels",
    "tackle": "defending", "defend": "defending", "marking": "defending",
    "finish": "finishing", "shot": "finishing", "goal": "finishing", "shoot": "finishing",
    "decision": "decision_making", "vision": "decision_making",
    "work": "work_rate", "press": "work_rate", "effort": "work_rate",
    "position": "positioning",
    "dribble": "dribbling", "beat": "dribbling", "skill": "dribbling",
    "strong": "physicality", "physical": "physicality", "duel": "physicality",
}

_POSITION_KEYWORDS: dict[str, str] = {
    "goalkeeper": "GK", "keeper": "GK",
    "left back": "LB", "right back": "RB", "centre back": "CB", "center back": "CB",
    "defender": "CB", "full back": "LB",
    "midfielder": "CM", "midfield": "CM", "playmaker": "AM",
    "winger": "RW", "striker": "ST", "forward": "ST", "centre forward": "ST",
}

_TEAM_NOTE_HINTS = {"team", "they", "formation", "pressing", "shape", "press high",
                    "back line", "midfield block", "equipo", "presionando"}


class MockAIProvider(AIProvider):
    async def transcribe_voice(self, audio_bytes: bytes, mime_type: str) -> str:
        # Deterministic placeholder; real transcription comes from Whisper.
        return "[voice note] number 8 great first touch, beat two players"

    async def parse_lineup(self, image_bytes: bytes, mime_type: str) -> list[ParsedPlayer]:
        return list(_DEMO_ROSTER)

    async def classify_notes(
        self, text: str, roster: list[ParsedPlayer]
    ) -> list[ClassifiedNote]:
        # A single message may qualify several players. Split into fragments only
        # when ≥2 of them carry their own player reference; otherwise treat the
        # whole message as one note (preserving the single-note behavior).
        fragments = _split_multi_player(text, roster)
        return [_classify_one(frag, roster) for frag in fragments]


def _classify_one(text: str, roster: list[ParsedPlayer]) -> ClassifiedNote:
    lowered = text.lower()

    # Team-level note? (no player reference + team keyword)
    number = _extract_number(lowered)
    name_match = _match_name(lowered, roster)
    position = _extract_position(lowered)
    is_team = (
        number is None
        and name_match is None
        and any(h in lowered for h in _TEAM_NOTE_HINTS)
    )

    if is_team:
        return ClassifiedNote(
            raw_quote=text,
            is_team_note=True,
            sentiment=None,
            skill_category=None,
            player_ref=None,
            confidence=0.9,
        )

    sentiment = _detect_sentiment(lowered)
    skill = _detect_skill(lowered)

    # Resolve player + score confidence.
    ref, confidence = _resolve_player(number, name_match, position, roster)

    return ClassifiedNote(
        raw_quote=text,
        is_team_note=False,
        sentiment=sentiment,
        skill_category=skill,
        player_ref=ref,
        confidence=confidence,
    )


# Connectors that tend to separate observations about different players.
_SPLIT_RE = re.compile(r"\s+but\s+|\s+pero\s+|;|,", re.IGNORECASE)


def _split_multi_player(text: str, roster: list[ParsedPlayer]) -> list[str]:
    """Split a message into per-player fragments, but only if it clearly covers
    more than one player. Returns [text] unchanged when it's a single note."""
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    if len(parts) < 2:
        return [text]
    # A fragment "has a player" if it names or numbers one.
    with_player = [
        p
        for p in parts
        if _extract_number(p.lower()) is not None or _match_name(p.lower(), roster)
    ]
    if len(with_player) < 2:
        return [text]
    return parts


# ── text parsing helpers ────────────────────────────────────────────────
def _extract_number(text: str) -> int | None:
    m = re.search(r"(?:number|#|no\.?|num|nº)\s*(\d{1,2})", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,2})\b", text)
    return int(m.group(1)) if m else None


def _match_name(text: str, roster: list[ParsedPlayer]) -> ParsedPlayer | None:
    """Find the roster player a note's text refers to by name.

    Scouts use surnames, drop accents, and mistype — so match each word of the
    note against each player's name fuzzily (shared with the real path via
    taxonomy.name_matches), not by raw substring. Exact (accent-insensitive)
    matches win over fuzzy ones to avoid a typo stealing a clean reference.
    """
    words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)  # word tokens, no digits
    exact: ParsedPlayer | None = None
    fuzzy: ParsedPlayer | None = None
    for p in roster:
        if not p.name:
            continue
        name_tokens = {normalize_name(t) for t in p.name.split()}
        for w in words:
            nw = normalize_name(w)
            if nw in name_tokens or nw == normalize_name(p.name):
                exact = exact or p
            elif name_matches(w, p.name):
                fuzzy = fuzzy or p
    return exact or fuzzy


def _extract_position(text: str) -> str | None:
    for kw, pos in _POSITION_KEYWORDS.items():
        if kw in text:
            return pos
    return None


def _detect_sentiment(text: str) -> str:
    pos = sum(1 for w in _POSITIVE_WORDS if w in text)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in text)
    # "too slow", "not good" style — negative wins ties toward caution only if present
    return SENTIMENT_POSITIVE if pos > neg else SENTIMENT_NEGATIVE


def _detect_skill(text: str) -> str:
    for kw, skill in _SKILL_HINTS.items():
        if kw in text:
            return normalize_skill(skill)
    return "other"


def _resolve_player(
    number: int | None,
    name_match: ParsedPlayer | None,
    position: str | None,
    roster: list[ParsedPlayer],
) -> tuple[PlayerMatch | None, float]:
    """Return (best-guess player reference, confidence 0..1).

    Confidence drops when a number/position is ambiguous across both teams,
    which is exactly when the bot should ask the agent to disambiguate.
    """
    # Strongest signal: an exact name match.
    if name_match is not None:
        return (
            PlayerMatch(
                number=name_match.number,
                name=name_match.name,
                position=name_match.position,
                side=name_match.side,
            ),
            0.95,
        )

    # Number reference: how many roster players share it?
    if number is not None:
        candidates = [p for p in roster if p.number == number]
        if len(candidates) == 1:
            p = candidates[0]
            return (
                PlayerMatch(p.number, p.name, p.position, p.side),
                0.9,
            )
        if len(candidates) > 1:
            # Same number on both teams — try position to break the tie.
            if position:
                refined = [p for p in candidates if p.position == position]
                if len(refined) == 1:
                    p = refined[0]
                    return PlayerMatch(p.number, p.name, p.position, p.side), 0.85
            # Genuinely ambiguous → low confidence, keep the number as a hint.
            return PlayerMatch(number=number), 0.4

    # Only a position reference: ambiguous unless a single player has it.
    if position is not None:
        candidates = [p for p in roster if p.position == position]
        if len(candidates) == 1:
            p = candidates[0]
            return PlayerMatch(p.number, p.name, p.position, p.side), 0.7
        if len(candidates) > 1:
            return PlayerMatch(position=position), 0.35

    # Nothing resolvable.
    return None, 0.2
