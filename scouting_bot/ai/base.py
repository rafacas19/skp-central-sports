"""AI provider contract + the structured types it returns.

The bot logic depends only on this module, never on a concrete vendor. This is
the seam that lets the MVP run fully on mocks today and swap in Claude + Whisper
by flipping USE_MOCK_AI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ParsedPlayer:
    """A player extracted from a lineup image."""

    number: int | None
    name: str
    position: str | None
    side: str  # "home" | "away"


@dataclass
class PlayerMatch:
    """A candidate player the classifier thinks a note refers to."""

    number: int | None = None
    name: str | None = None
    position: str | None = None
    side: str | None = None


@dataclass
class ClassifiedNote:
    """The structured result of classifying a single observation."""

    raw_quote: str
    is_team_note: bool
    sentiment: str | None  # positive | negative ; None for team notes
    skill_category: str | None
    player_ref: PlayerMatch | None  # who the note is about (best guess)
    confidence: float  # 0..1 — below threshold ⇒ bot asks the agent


class AIProvider(Protocol):
    """The three intelligence tasks the bot needs."""

    async def transcribe_voice(self, audio_bytes: bytes, mime_type: str) -> str:
        """Voice note → text. Text messages bypass this and are forwarded as-is."""
        ...

    async def parse_lineup(self, image_bytes: bytes, mime_type: str) -> list[ParsedPlayer]:
        """Lineup image → list of players for both teams."""
        ...

    async def classify_note(
        self,
        text: str,
        roster: list[ParsedPlayer],
    ) -> ClassifiedNote:
        """Free text → structured observation, resolving the player against the roster."""
        ...
