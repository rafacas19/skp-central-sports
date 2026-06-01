"""Product-level data model (dataclasses).

Mirrors the "Core Concepts" section of the product outline:
Session → Match → Team/Roster → Player → Observation.

These are plain dataclasses; persistence lives in storage.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Session lifecycle states.
SESSION_ACTIVE = "active"
SESSION_ENDED = "ended"

HOME = "home"
AWAY = "away"


@dataclass
class Player:
    """A roster entry within a single match (no cross-match identity in MVP)."""

    id: int | None
    session_id: int
    side: str  # HOME | AWAY
    number: int | None
    name: str
    position: str | None
    is_target: bool = False


@dataclass
class Observation:
    """The atomic scouting note."""

    id: int | None
    session_id: int
    player_id: int | None  # None ⇒ team-level note
    side: str | None  # set for team-level notes; mirrors player side otherwise
    sentiment: str | None  # positive | negative ; None for free-text team notes
    skill_category: str | None
    raw_quote: str
    created_at: str  # ISO-8601 timestamp


@dataclass
class Session:
    """One match-scouting episode owned by one agent."""

    id: int | None
    agent_chat_id: int
    home_team: str
    away_team: str
    label: str | None  # free-text competition/date label
    state: str = SESSION_ACTIVE
    roster_confirmed: bool = False
    created_at: str = ""
    last_activity_at: str = ""
    ended_at: str | None = None

    players: list[Player] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
