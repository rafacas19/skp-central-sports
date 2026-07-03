"""AI provider contract + the structured types it returns.

The bot logic depends only on this module, never on a concrete vendor. This is
the seam that lets the MVP run fully on mocks today and swap in Claude + Whisper
by flipping USE_MOCK_AI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class PlayerMatch:
    """The identity a note refers to, as extracted from the text.

    `team` is the actual team name when the scout stated it (e.g. "América");
    `side` ("home"/"away") is set when it can be inferred from the match. Either
    may be None when the scout didn't say which team."""

    number: int | None = None
    name: str | None = None
    position: str | None = None
    side: str | None = None
    team: str | None = None


@dataclass
class ClassifiedNote:
    """The structured result of classifying a single observation.

    Identity-only: the AI extracts WHO the note is about (and whether it's a team
    note), never an evaluation. Sentiment/skill are no longer produced — ratings
    are manual.

    For a substitution ("entra X y sale Y"), `is_substitution` is True and
    `player_ref` identifies the ENTERING player (so later observations attach to
    them); the exiting player is left in `raw_quote` only."""

    raw_quote: str
    is_team_note: bool
    player_ref: PlayerMatch | None  # who the note is about
    confidence: float  # 0..1 — below threshold ⇒ bot asks the agent
    is_substitution: bool = False


class AIProvider(Protocol):
    """The intelligence tasks the bot needs."""

    async def transcribe_voice(self, audio_bytes: bytes, mime_type: str) -> str:
        """Voice note → text. Text messages bypass this and are forwarded as-is."""
        ...

    async def classify_notes(
        self,
        text: str,
        home_team: str,
        away_team: str,
    ) -> list[ClassifiedNote]:
        """Free text → one or more structured observations (identity only).

        A single message may qualify several players ("#10 great vision but #4
        too slow") or none. Returns one ClassifiedNote per distinct player/team
        observation, in reading order; an empty list when nothing is classifiable.
        The two team names let the classifier map a stated team ("América") to a
        side and decide whether a number-only reference is team-ambiguous.
        """
        ...

    async def summarize_player(self, observations: list[dict]) -> str:
        """Cross-match raw observations → a short Spanish scouting profile.

        Each dict carries {date, match, team, opponent, position, number,
        observation, rating, source, scout}. Returns prose describing patterns,
        strengths, concerns, and a recommendation."""
        ...
