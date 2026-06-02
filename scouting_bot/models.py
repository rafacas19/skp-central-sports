"""Tortoise ORM models — the persisted domain model.

Mirrors the "Core Concepts" of the product outline:
Session → Team/Roster → Player → Observation.

These models are the single source of truth for the schema (managed by Aerich).
`report.py` and `service.py` operate on already-fetched instances, so the
storage layer prefetches `players`/`observations` before handing a Session to
them — letting that code read `session.players` / `session.observations` as
plain lists without awaiting.
"""

from __future__ import annotations

from tortoise import fields
from tortoise.models import Model

# Session lifecycle states.
SESSION_ACTIVE = "active"
SESSION_ENDED = "ended"

HOME = "home"
AWAY = "away"


class Session(Model):
    """One match-scouting episode owned by one agent."""

    id = fields.IntField(primary_key=True)
    agent_chat_id = fields.BigIntField()
    home_team = fields.TextField()
    away_team = fields.TextField()
    label = fields.TextField(null=True)  # free-text competition/date label
    state = fields.CharField(max_length=16, default=SESSION_ACTIVE)
    roster_confirmed = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_activity_at = fields.DatetimeField(auto_now_add=True)
    ended_at = fields.DatetimeField(null=True)

    players: fields.ReverseRelation["Player"]
    observations: fields.ReverseRelation["Observation"]

    class Meta:
        table = "sessions"
        indexes = (("agent_chat_id", "state"),)

    def __str__(self) -> str:
        return f"Session({self.id}: {self.home_team} vs {self.away_team})"


class Player(Model):
    """A roster entry within a single match (no cross-match identity in MVP)."""

    id = fields.IntField(primary_key=True)
    session: fields.ForeignKeyRelation[Session] = fields.ForeignKeyField(
        "models.Session", related_name="players", on_delete=fields.CASCADE
    )
    side = fields.CharField(max_length=8)  # HOME | AWAY
    number = fields.IntField(null=True)
    name = fields.TextField()
    position = fields.CharField(max_length=80, null=True)
    is_target = fields.BooleanField(default=False)

    class Meta:
        table = "players"

    @property
    def session_id_value(self) -> int:
        # Tortoise exposes the FK id as `session_id` automatically; this is a
        # readable alias used in a couple of places.
        return self.session_id  # type: ignore[attr-defined]


class Observation(Model):
    """The atomic scouting note."""

    id = fields.IntField(primary_key=True)
    session: fields.ForeignKeyRelation[Session] = fields.ForeignKeyField(
        "models.Session", related_name="observations", on_delete=fields.CASCADE
    )
    # player is nullable: a null player_id ⇒ a team-level note.
    player: fields.ForeignKeyNullableRelation[Player] = fields.ForeignKeyField(
        "models.Player", related_name="observations", null=True, on_delete=fields.SET_NULL
    )
    side = fields.CharField(max_length=8, null=True)
    sentiment = fields.CharField(max_length=16, null=True)
    skill_category = fields.CharField(max_length=32, null=True)
    raw_quote = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "observations"
