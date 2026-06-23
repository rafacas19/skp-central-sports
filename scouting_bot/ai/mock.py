"""Deterministic mock AI provider.

No API keys required. It is intentionally *real enough* to exercise every branch
of the observation-first flow:

  - transcribe_voice: returns a deterministic placeholder transcript
  - classify_notes: extracts player IDENTITY only (name / number / position /
    team) from each fragment of a message that may qualify several players, and
    lowers confidence when a number-only reference doesn't say which team (so the
    team-ambiguity ask triggers). It does NOT evaluate (no sentiment/skill) —
    ratings are manual.
  - summarize_player: deterministic Spanish profile from raw observations.

Swap to RealAIProvider (Claude + Whisper) by setting USE_MOCK_AI=false.
"""

from __future__ import annotations

import re

from ..taxonomy import normalize_name
from .base import AIProvider, ClassifiedNote, PlayerMatch

_POSITION_KEYWORDS: dict[str, str] = {
    # Spanish (primary) + a few English aliases.
    "arquero": "GK", "portero": "GK", "goalkeeper": "GK", "keeper": "GK",
    "lateral": "LB", "lateral izquierdo": "LB", "lateral derecho": "RB",
    "left back": "LB", "right back": "RB",
    "central": "CB", "defensa": "CB", "centre back": "CB", "center back": "CB",
    "volante": "CM", "mediocampista": "CM", "midfielder": "CM", "midfield": "CM",
    "enganche": "AM", "playmaker": "AM",
    "extremo": "RW", "winger": "RW",
    "delantero": "ST", "punta": "ST", "striker": "ST", "forward": "ST",
}

_TEAM_NOTE_HINTS = {
    "el equipo", "equipo juega", "formación", "formacion", "presionan",
    "presionando", "defienden", "línea de", "linea de", "bloque",
    "juegan", "the team", "they play", "formation", "pressing",
}


class MockAIProvider(AIProvider):
    async def transcribe_voice(self, audio_bytes: bytes, mime_type: str) -> str:
        # Deterministic placeholder; real transcription comes from Whisper.
        return "#8 muy rápido en el 1vs1, buen primer toque"

    async def classify_notes(
        self, text: str, home_team: str, away_team: str
    ) -> list[ClassifiedNote]:
        fragments = _split_multi_player(text, home_team, away_team)
        notes = [_classify_one(frag, home_team, away_team) for frag in fragments]
        return [n for n in notes if n is not None]

    async def summarize_player(self, observations: list[dict]) -> str:
        n = len(observations)
        matches = {o.get("match") for o in observations if o.get("match")}
        quotes = [o.get("observation", "") for o in observations][:3]
        joined = "; ".join(q for q in quotes if q)
        return (
            f"Resumen: {n} observación(es) en {len(matches)} partido(s). "
            f"Notas recurrentes: {joined}. Recomendación: seguir observando."
        )


def _classify_one(
    text: str, home_team: str, away_team: str
) -> ClassifiedNote | None:
    stripped = text.strip()
    if not stripped:
        return None
    lowered = stripped.lower()

    team = _match_team(lowered, home_team, away_team)
    side = _side_for(team, home_team, away_team)
    number = _extract_number(lowered)
    position = _extract_position(lowered)
    name = _extract_name(stripped, home_team, away_team)

    # Team-level note: a team-tactics keyword and no individual player NAME.
    # (A stray number like "línea de 4" doesn't make it a player note.)
    if name is None and any(h in lowered for h in _TEAM_NOTE_HINTS):
        return ClassifiedNote(
            raw_quote=stripped,
            is_team_note=True,
            player_ref=PlayerMatch(team=team, side=side),
            confidence=0.9,
        )

    ref = PlayerMatch(
        number=number, name=name, position=position, side=side, team=team
    )

    # Confidence: a name, or a number with a known team, is confident. A
    # number-only reference with no team is ambiguous → ask which team.
    if name:
        confidence = 0.95
    elif number is not None and team:
        confidence = 0.9
    elif number is not None:
        confidence = 0.4  # team-ambiguous
    elif position and team:
        confidence = 0.7
    else:
        confidence = 0.3

    if name is None and number is None and position is None and team is None:
        return None  # nothing to attach an observation to
    return ClassifiedNote(
        raw_quote=stripped, is_team_note=False, player_ref=ref, confidence=confidence
    )


# Connectors that tend to separate observations about different players.
_SPLIT_RE = re.compile(r"\s+but\s+|\s+pero\s+|;", re.IGNORECASE)


def _split_multi_player(text: str, home_team: str, away_team: str) -> list[str]:
    """Split a message into per-player fragments, only when it clearly covers
    more than one player (each side carries its own identity). Otherwise [text].

    Note: we split on ' but '/' pero '/';' but NOT bare commas, since commas are
    used WITHIN a single observation ("América, #7, extremo, muy rápido")."""
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    if len(parts) < 2:
        return [text]

    def has_player(fragment: str) -> bool:
        return (
            _extract_number(fragment.lower()) is not None
            or _extract_name(fragment, home_team, away_team) is not None
        )

    if sum(1 for p in parts if has_player(p)) < 2:
        return [text]
    return parts


# ── text parsing helpers ────────────────────────────────────────────────
def _extract_number(text: str) -> int | None:
    m = re.search(r"(?:number|#|n\.?º?|num|nº)\s*(\d{1,2})", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,2})\b", text)
    return int(m.group(1)) if m else None


def _match_team(lowered: str, home_team: str, away_team: str) -> str | None:
    for team in (home_team, away_team):
        if team and normalize_name(team) in normalize_name(lowered):
            return team
    return None


def _side_for(team: str | None, home_team: str, away_team: str) -> str | None:
    if team is None:
        return None
    if normalize_name(team) == normalize_name(home_team):
        return "home"
    if normalize_name(team) == normalize_name(away_team):
        return "away"
    return None


def _extract_position(text: str) -> str | None:
    # Longer keywords first ("lateral izquierdo" before "lateral").
    for kw in sorted(_POSITION_KEYWORDS, key=len, reverse=True):
        if kw in text:
            return _POSITION_KEYWORDS[kw]
    return None


# Words that are never a player's name (positions, fillers, team-note hints).
_NOT_NAME = set(_POSITION_KEYWORDS) | {
    "el", "la", "de", "del", "muy", "buen", "buena", "bueno", "gana", "todos",
    "los", "las", "en", "se", "con", "por", "dentro", "the", "of",
}


def _extract_name(text: str, home_team: str, away_team: str) -> str | None:
    """Heuristic: the first capitalized token that isn't a team name, position,
    or filler. Good enough for the deterministic mock; the real provider uses
    Claude. Returns None if no plausible name token is present."""
    team_norms = {normalize_name(home_team), normalize_name(away_team)}
    for raw in re.findall(r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+", text):
        norm = normalize_name(raw)
        if norm in team_norms or norm in _NOT_NAME or raw.isupper():
            continue
        return raw
    return None
