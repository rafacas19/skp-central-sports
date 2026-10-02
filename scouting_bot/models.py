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

# Where a match was created. Bot matches drive the Telegram flow (the active
# match, the auto-nudge); web matches are opened and closed on the dashboard and
# must never be picked up by the bot as the match a note belongs to.
ORIGIN_BOT = "bot"
ORIGIN_WEB = "web"

# Observation source (how the note arrived).
SOURCE_TEXT = "text"
SOURCE_VOICE = "voice"
SOURCE_PHOTO = "photo"
SOURCE_WEB = "web"  # a score sheet filled in on the dashboard

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


# Contact follow-up ("CRM") statuses, in funnel order. A player with no status
# has not been contacted yet, so NULL reads as CONTACT_NONE everywhere and no
# existing row needs a value. `CONTACT_DISCARDED` is the dead end *after* talking
# to someone — the scouting `decision_status` is a separate axis: a player can be
# "A firmar" on the pitch and "Descartado" in the conversation.
CONTACT_NONE = "Sin contactar"
CONTACT_REACHED = "Contactado"
CONTACT_TALKING = "En conversación"
CONTACT_MEETING = "Reunión agendada"
CONTACT_AGREED = "Acuerdo"
CONTACT_DISCARDED = "Descartado"
CONTACT_STATUSES = (
    CONTACT_NONE,
    CONTACT_REACHED,
    CONTACT_TALKING,
    CONTACT_MEETING,
    CONTACT_AGREED,
    CONTACT_DISCARDED,
)


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
    origin = fields.CharField(max_length=8, default=ORIGIN_BOT)  # ORIGIN_BOT | ORIGIN_WEB
    # Lineup formation per side ("4-3-3"), set from the dashboard lineup editor.
    home_formation = fields.CharField(max_length=8, null=True)
    away_formation = fields.CharField(max_length=8, null=True)
    # Optional match metadata (parsed from `/nuevo … | campo=valor`).
    scout_name = fields.TextField(null=True)
    competition = fields.TextField(null=True)
    category = fields.TextField(null=True)  # typed by the scout on /nuevo
    location = fields.TextField(null=True)
    # Categories DERIVED from the team names ("Santa Fe U18" → "Sub-18"), one per
    # side. Kept apart from `category` above so a derived value never overwrites
    # what the scout typed — see categories.split_category.
    home_team_category = fields.TextField(null=True)
    away_team_category = fields.TextField(null=True)
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
    evaluations: fields.ReverseRelation["Evaluation"]
    lineup: fields.ReverseRelation["MatchPlayer"]

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
    team = fields.TextField(null=True)  # the club, with any category split off
    normalized_team = fields.TextField(null=True)
    category = fields.TextField(null=True)  # derived from the team name, may be null
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
    # Contact follow-up. `contact_status` is one of CONTACT_STATUSES (NULL ⇒ never
    # contacted); `last_contact_at` is the day of the most recent interaction and
    # `contact_notes` is what came out of it. Set from the dashboard only — the
    # bot captures matches, not conversations.
    contact_status = fields.CharField(max_length=32, null=True)
    last_contact_at = fields.DateField(null=True)
    contact_notes = fields.TextField(null=True)
    is_temporary = fields.BooleanField(default=False)
    photo_file_id = fields.TextField(null=True)  # Telegram file_id (MVP storage)
    video_url = fields.TextField(null=True)  # highlights link (YouTube, Drive…)
    notes = fields.TextField(null=True)
    # Cached dashboard AI summary. The obs-count watermark marks which state it
    # was generated from; when it drifts, the dashboard refreshes in the
    # background (see dashboard/summaries.py).
    ai_summary = fields.TextField(null=True)
    ai_summary_obs_count = fields.IntField(null=True)
    # Fingerprint of the score sheets the summary saw: re-scoring a match changes
    # no observation count, so the count alone would never flag it stale.
    ai_summary_marker = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    observations: fields.ReverseRelation["Observation"]
    squad_memberships: fields.ReverseRelation["SquadMember"]
    evaluations: fields.ReverseRelation["Evaluation"]
    lineups: fields.ReverseRelation["MatchPlayer"]
    profile_reports: fields.ReverseRelation["ProfileReport"]
    web_photo: fields.BackwardOneToOneRelation["ProspectPhoto"]

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


class Squad(Model):
    """A national-team call-up list ("selección"), owned by one scout.

    A squad is deliberately *not* a team. A player's `team` stays his club and
    being in "Colombia Sub-15" is a membership beside it — writing the national
    team into `team` would fork one player into two prospects, since identity
    keys on (agent_chat_id, normalized_name, normalized_team).

    `name` is the entity ("Colombia") and `category` the age group ("Sub-15"),
    split off the typed name by `categories.split_category`, so "Selección
    Colombia U15" and "colombia sub 15" reach the same squad. Uniqueness of
    (chat, name, category) is enforced in the dashboard instead of by a
    constraint: a NULL category would defeat a UNIQUE index in Postgres.
    """

    id = fields.IntField(primary_key=True)
    agent_chat_id = fields.BigIntField()
    name = fields.TextField()
    normalized_name = fields.TextField()  # taxonomy.normalize_name(name)
    category = fields.TextField(null=True)  # "Sub-15", may be absent
    created_at = fields.DatetimeField(auto_now_add=True)

    members: fields.ReverseRelation["SquadMember"]

    class Meta:
        table = "squads"
        indexes = (("agent_chat_id", "normalized_name"),)

    def __str__(self) -> str:
        return f"Squad({self.id}: {self.name} {self.category or ''})".strip()


class SquadMember(Model):
    """One player's place in one squad.

    Membership is a row, not a column, so a player can be called up by several
    squads over time (Sub-15 this year, Sub-17 the next) and the earlier lists
    stay intact. It carries no scouting data of its own: every column the client
    reads off a squad list already lives on the prospect.
    """

    id = fields.IntField(primary_key=True)
    squad: fields.ForeignKeyRelation[Squad] = fields.ForeignKeyField(
        "models.Squad", related_name="members", on_delete=fields.CASCADE
    )
    prospect: fields.ForeignKeyRelation[Prospect] = fields.ForeignKeyField(
        "models.Prospect", related_name="squad_memberships", on_delete=fields.CASCADE
    )
    added_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "squad_members"
        unique_together = (("squad", "prospect"),)


class Evaluation(Model):
    """One player's score sheet for one match, filled in on the dashboard.

    `profile` names the position profile (profiles.PROFILES) the scores belong
    to; `scores` maps its criterion codes to whole 1–5 scores ({"1.1": 4, …}),
    always read and written as a set, so it is a JSON document rather than one
    row per criterion. A player with no profile (a goalkeeper, until the client
    sends that sheet) is rated with a single `rating` and no scores.

    `rating` is the match rating the sheet stands for — the mean of its scores,
    or the single rating — and is mirrored onto `observation`, an ordinary rated
    Observation, so the rating history, the decision and every export read a
    web score exactly like a rating captured by the bot.
    """

    id = fields.IntField(primary_key=True)
    session: fields.ForeignKeyRelation[Session] = fields.ForeignKeyField(
        "models.Session", related_name="evaluations", on_delete=fields.CASCADE
    )
    prospect: fields.ForeignKeyRelation[Prospect] = fields.ForeignKeyField(
        "models.Prospect", related_name="evaluations", on_delete=fields.CASCADE
    )
    profile = fields.CharField(max_length=32, null=True)  # profiles key
    scores = fields.JSONField(default=dict)
    build_ok = fields.BooleanField(null=True)  # contextura as the profile asks
    height_ok = fields.BooleanField(null=True)  # estatura as the profile asks
    rating = fields.FloatField(null=True)
    note = fields.TextField(null=True)
    observation: fields.ForeignKeyNullableRelation[Observation] = fields.ForeignKeyField(
        "models.Observation", related_name="evaluations", null=True,
        on_delete=fields.SET_NULL,
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "evaluations"
        unique_together = (("session", "prospect"),)


class MatchPlayer(Model):
    """One player in one match's lineup, entered on the dashboard.

    `slot` is the index into the side's formation (formations.FORMATIONS); a
    NULL slot is a substitute on the bench. `shirt_number` is the dorsal worn in
    this match, which can differ from the player's habitual one.
    """

    id = fields.IntField(primary_key=True)
    session: fields.ForeignKeyRelation[Session] = fields.ForeignKeyField(
        "models.Session", related_name="lineup", on_delete=fields.CASCADE
    )
    prospect: fields.ForeignKeyRelation[Prospect] = fields.ForeignKeyField(
        "models.Prospect", related_name="lineups", on_delete=fields.CASCADE
    )
    side = fields.CharField(max_length=8)  # HOME | AWAY
    slot = fields.IntField(null=True)
    shirt_number = fields.IntField(null=True)
    # Substitutions. The player who came on takes the slot and remembers whom he
    # replaced (`came_on_for_id`, a prospect id); the one who went off moves to
    # the bench with `subbed_off`. `sub_minute` is the minute of that change.
    came_on_for_id = fields.IntField(null=True)
    subbed_off = fields.BooleanField(default=False)
    sub_minute = fields.IntField(null=True)

    class Meta:
        table = "match_players"
        unique_together = (("session", "prospect"),)


class ProfileReport(Model):
    """The written part of a player's profile report, for one profile.

    Claude drafts `summary` and one paragraph per section (`sections`, keyed by
    section number as a string); the scout can edit them. `watermark`
    fingerprints the evaluations the text was written from: when it no longer
    matches, an unedited text is redrafted in the background, while an edited
    one is left alone and the page offers to regenerate it.
    """

    id = fields.IntField(primary_key=True)
    prospect: fields.ForeignKeyRelation[Prospect] = fields.ForeignKeyField(
        "models.Prospect", related_name="profile_reports", on_delete=fields.CASCADE
    )
    profile = fields.CharField(max_length=32)
    summary = fields.TextField(null=True)
    sections = fields.JSONField(default=dict)
    edited = fields.BooleanField(default=False)
    watermark = fields.TextField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "profile_reports"
        unique_together = (("prospect", "profile"),)


class ProspectPhoto(Model):
    """A photo uploaded from the dashboard, stored in the database.

    Kept out of `prospects` on purpose: every list page loads prospects in bulk,
    and a few megabytes of image per row would ride along on each of those
    queries. The Render disk is wiped on every deploy, so the bytes live in
    Postgres rather than on the filesystem. When present it is shown instead
    of the Telegram photo.
    """

    prospect: fields.OneToOneRelation[Prospect] = fields.OneToOneField(
        "models.Prospect", related_name="web_photo", on_delete=fields.CASCADE,
        primary_key=True,
    )
    data = fields.BinaryField()
    mime = fields.CharField(max_length=32)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "prospect_photos"
