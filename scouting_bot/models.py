"""Tortoise ORM models — the persisted domain model.

Observation-first design:
Session (match) → Observation → Prospect (cross-match player identity).

These models are the single source of truth for the schema (managed by Aerich).
The storage layer prefetches a Session's `observations` (and their `prospect`)
before handing it to `report.py`, so that code reads `session.observations` as a
plain list without awaiting.
"""

from __future__ import annotations

from tortoise import fields
from tortoise.models import Model

# Session lifecycle states.
SESSION_ACTIVE = "active"
SESSION_ENDED = "ended"

HOME = "home"
AWAY = "away"

# Observation source (how the note arrived).
SOURCE_TEXT = "text"
SOURCE_VOICE = "voice"
SOURCE_PHOTO = "photo"

# Player decision statuses (end-of-month workflow).
DECISION_PENDING = "Pendiente"
DECISION_WATCH = "Seguir observando"
DECISION_ADVANCE = "Avanzar"
DECISION_DISCARD = "Descartar"
DECISION_STATUSES = (
    DECISION_PENDING,
    DECISION_WATCH,
    DECISION_ADVANCE,
    DECISION_DISCARD,
)


class Session(Model):
    """One match-scouting episode owned by one agent."""

    id = fields.IntField(primary_key=True)
    agent_chat_id = fields.BigIntField()
    home_team = fields.TextField()
    away_team = fields.TextField()
    label = fields.TextField(null=True)  # free-text competition/date label
    state = fields.CharField(max_length=16, default=SESSION_ACTIVE)
    # Optional match metadata (parsed from `/nuevo … | campo=valor`).
    scout_name = fields.TextField(null=True)
    competition = fields.TextField(null=True)
    category = fields.TextField(null=True)
    location = fields.TextField(null=True)
    match_date = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_activity_at = fields.DatetimeField(auto_now_add=True)
    ended_at = fields.DatetimeField(null=True)

    observations: fields.ReverseRelation["Observation"]

    class Meta:
        table = "sessions"
        indexes = (("agent_chat_id", "state"),)

    def __str__(self) -> str:
        return f"Session({self.id}: {self.home_team} vs {self.away_team})"


class ScoutProfile(Model):
    """Per-chat scout identity. One scout per Telegram chat; `/yo` sets the name
    used in reports. Keyed by the chat id so it persists across matches."""

    agent_chat_id = fields.BigIntField(primary_key=True)
    name = fields.TextField()

    class Meta:
        table = "scout_profiles"


class Prospect(Model):
    """A scouted player with a cross-match identity, owned by one scout (chat).

    Keyed by (agent_chat_id, normalized_name, normalized_team) so the same player
    seen in different matches — possibly with different shirt numbers — is one
    record. Holds bio, the latest manual rating, the decision status, and (for
    `/foto` / number-only notes) a temporary flag until the scout names them.
    """

    id = fields.IntField(primary_key=True)
    agent_chat_id = fields.BigIntField()
    name = fields.TextField()  # may be "" for a temporary / number-only profile
    normalized_name = fields.TextField()  # taxonomy.normalize_name(name)
    team = fields.TextField(null=True)
    normalized_team = fields.TextField(null=True)
    position = fields.CharField(max_length=80, null=True)
    age = fields.IntField(null=True)
    height_cm = fields.IntField(null=True)
    latest_rating = fields.FloatField(null=True)  # manual, 1–10 (decimals allowed)
    decision_status = fields.CharField(max_length=24, null=True)  # DECISION_STATUSES
    is_temporary = fields.BooleanField(default=False)
    photo_file_id = fields.TextField(null=True)  # Telegram file_id (MVP storage)
    notes = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    observations: fields.ReverseRelation["Observation"]

    class Meta:
        table = "prospects"
        indexes = (("agent_chat_id", "normalized_name", "normalized_team"),)

    def __str__(self) -> str:
        return f"Prospect({self.id}: {self.name or '?'} / {self.team or '?'})"


class Observation(Model):
    """The atomic scouting note."""

    id = fields.IntField(primary_key=True)
    session: fields.ForeignKeyRelation[Session] = fields.ForeignKeyField(
        "models.Session", related_name="observations", on_delete=fields.CASCADE
    )
    # Cross-match identity link (a null prospect_id ⇒ a team-level note).
    prospect: fields.ForeignKeyNullableRelation[Prospect] = fields.ForeignKeyField(
        "models.Prospect", related_name="observations", null=True, on_delete=fields.SET_NULL
    )
    side = fields.CharField(max_length=8, null=True)
    # Identity snapshot, so a row is a complete CSV record on its own.
    team = fields.TextField(null=True)
    player_name = fields.TextField(null=True)
    player_number = fields.IntField(null=True)
    player_position = fields.CharField(max_length=80, null=True)
    source = fields.CharField(max_length=8, null=True)  # text | voice | photo
    rating = fields.FloatField(null=True)  # manual, inline ("valoración 7")
    is_team_note = fields.BooleanField(default=False)
    raw_quote = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "observations"
