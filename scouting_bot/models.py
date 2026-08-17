"""Tortoise ORM models — the persisted domain model.

Observation-first design:
Session (match) → Observation → Prospect (cross-match player identity).

These models are the single source of truth for the schema (managed by Aerich).
The storage layer prefetches a Session's `observations` (and their `prospect`)
before handing it to `report.py`, so that code reads `session.observations` as a
plain list without awaiting.
"""

from __future__ import annotations

from datetime import date

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

# Player decision statuses (manual /decision workflow + report buttons).
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

# Auto-decision derived from the manual 1–5 rating (client-specified mapping).
# This is the decision shown in reports whenever a player has a rating; the scout
# no longer needs a separate command in the main flow.
RATING_DECISIONS = {
    1: "A descartar",
    2: "A seguir",
    3: "Interesante",
    4: "Muy interesante",
    5: "A firmar",
}


# Preferred foot — the three values every scouting product uses.
FOOT_LEFT = "izquierdo"
FOOT_RIGHT = "derecho"
FOOT_BOTH = "ambidiestro"
FEET = (FOOT_LEFT, FOOT_RIGHT, FOOT_BOTH)


def decision_for_rating(rating: float | None) -> str | None:
    """Map a 1–5 rating (rounded to the nearest whole) onto its decision label.

    Returns None when there's no rating. Out-of-range values are clamped so a
    stray 0 or 6 still yields a sensible edge decision."""
    if rating is None:
        return None
    bucket = min(5, max(1, round(rating)))
    return RATING_DECISIONS[bucket]


def current_age(
    birth_year: int | None, stated_age: int | None, today: date | None = None
) -> int | None:
    """The age to display: derived from the birth year when we have one, else
    whatever age the scout stated.

    Only the birth *year* is captured (no full date of birth), so this can be off
    by one before the player's birthday — the trade-off for a number that never
    goes stale, and the same convention youth football uses ("categoría 2008").
    An implausible year is ignored rather than shown."""
    if birth_year:
        age = (today or date.today()).year - birth_year
        if 0 <= age <= 60:
            return age
    return stated_age


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
    # Match clock. Each half's wall-clock start is stamped when the scout sends
    # /primer_tiempo or /segundo_tiempo; the current minute is derived from these
    # (see ScoutingService.current_minute). Null until the scout starts the half.
    first_half_started_at = fields.DatetimeField(null=True)
    second_half_started_at = fields.DatetimeField(null=True)
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
    # Scouting bio, all optional and all editable from the dashboard. `birth_year`
    # supersedes `age` when set (an age captured mid-season goes stale; a birth
    # year doesn't, and youth football groups players by it) — see `current_age`.
    birth_year = fields.IntField(null=True)
    preferred_foot = fields.CharField(max_length=16, null=True)  # FEET
    shirt_number = fields.IntField(null=True)  # habitual dorsal
    nationality = fields.TextField(null=True)
    weight_kg = fields.IntField(null=True)
    origin_club = fields.TextField(null=True)  # club/academia de procedencia
    agent_name = fields.TextField(null=True)
    agent_phone = fields.TextField(null=True)
    market_value_usd = fields.IntField(null=True)  # scout's estimate, whole USD
    contract_year = fields.IntField(null=True)  # contrato hasta
    latest_rating = fields.FloatField(null=True)  # manual, 1–5 (decimals allowed)
    decision_status = fields.CharField(max_length=24, null=True)  # DECISION_STATUSES
    is_temporary = fields.BooleanField(default=False)
    photo_file_id = fields.TextField(null=True)  # Telegram file_id (MVP storage)
    notes = fields.TextField(null=True)
    # Cached dashboard AI summary. The obs-count watermark marks which state it
    # was generated from; when it drifts, the dashboard refreshes in the
    # background (see dashboard/summaries.py).
    ai_summary = fields.TextField(null=True)
    ai_summary_obs_count = fields.IntField(null=True)
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
    minute = fields.IntField(null=True)  # match minute (from the clock), null if not running
    is_team_note = fields.BooleanField(default=False)
    is_substitution = fields.BooleanField(default=False)  # "entra … sale …"
    raw_quote = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "observations"
