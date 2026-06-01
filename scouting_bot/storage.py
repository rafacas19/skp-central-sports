"""PostgreSQL persistence layer (psycopg 3 + connection pool).

The repository functions are the only place that touches SQL, so the rest of the
codebase stays storage-agnostic. Postgres is used everywhere — local dev, tests,
and production (Render) — so there is a single code path and no dialect drift.

Resilient sessions: state is always in the database, so a bot restart or an
agent's phone dying never loses an active session — the agent just keeps sending
notes. On Render this requires a managed Postgres (the local filesystem is
ephemeral); see render.yaml.
"""

from __future__ import annotations

from datetime import datetime, timezone

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .models import (
    SESSION_ACTIVE,
    SESSION_ENDED,
    Observation,
    Player,
    Session,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_chat_id     BIGINT NOT NULL,
    home_team         TEXT NOT NULL,
    away_team         TEXT NOT NULL,
    label             TEXT,
    state             TEXT NOT NULL DEFAULT 'active',
    roster_confirmed  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL,
    last_activity_at  TIMESTAMPTZ NOT NULL,
    ended_at          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS players (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    side        TEXT NOT NULL,
    number      INTEGER,
    name        TEXT NOT NULL,
    position    TEXT,
    is_target   BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    player_id       INTEGER REFERENCES players(id) ON DELETE SET NULL,
    side            TEXT,
    sentiment       TEXT,
    skill_category  TEXT,
    raw_quote       TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_agent_state ON sessions(agent_chat_id, state);
CREATE INDEX IF NOT EXISTS idx_players_session ON players(session_id);
CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);

-- Enforce one active session per agent at the database level (defense in depth;
-- the service layer also checks). A partial unique index allows many ended
-- sessions per agent but at most one 'active'.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_one_active_per_agent
    ON sessions(agent_chat_id) WHERE state = 'active';
"""


def _now() -> str:
    """ISO-8601 UTC timestamp string.

    Kept as a string (not datetime) because service.py and the models treat
    timestamps as ISO strings throughout; Postgres TIMESTAMPTZ columns accept
    and return them transparently via psycopg.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    """Postgres-backed repository.

    Args:
        dsn: a libpq connection string / URL (DATABASE_URL). Render provides this
             for its managed Postgres; locally point it at a dev/test database.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL is not set — Postgres connection string required "
                "(see .env.example)."
            )
        self._dsn = dsn
        # min_size=1 keeps a warm connection; the pool transparently reconnects
        # if Postgres restarts (common on managed platforms).
        self._pool = ConnectionPool(
            dsn, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=True
        )
        self._init_schema()

    def _init_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)

    def close(self) -> None:
        self._pool.close()

    # ── Sessions ────────────────────────────────────────────────────────
    def create_session(
        self, agent_chat_id: int, home_team: str, away_team: str, label: str | None
    ) -> Session:
        ts = _now()
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO sessions
                   (agent_chat_id, home_team, away_team, label, state,
                    roster_confirmed, created_at, last_activity_at)
                   VALUES (%s, %s, %s, %s, %s, FALSE, %s, %s)
                   RETURNING id""",
                (agent_chat_id, home_team, away_team, label, SESSION_ACTIVE, ts, ts),
            ).fetchone()
        return self.get_session(row["id"])

    def get_active_session(self, agent_chat_id: int) -> Session | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE agent_chat_id = %s AND state = %s "
                "ORDER BY id DESC LIMIT 1",
                (agent_chat_id, SESSION_ACTIVE),
            ).fetchone()
        return self._hydrate_session(row) if row else None

    def get_session(self, session_id: int) -> Session:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = %s", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"session {session_id} not found")
        return self._hydrate_session(row)

    def touch_session(self, session_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE sessions SET last_activity_at = %s WHERE id = %s",
                (_now(), session_id),
            )

    def confirm_roster(self, session_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE sessions SET roster_confirmed = TRUE, last_activity_at = %s "
                "WHERE id = %s",
                (_now(), session_id),
            )

    def end_session(self, session_id: int) -> None:
        ts = _now()
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE sessions SET state = %s, ended_at = %s, last_activity_at = %s "
                "WHERE id = %s",
                (SESSION_ENDED, ts, ts, session_id),
            )

    def stale_active_sessions(self, older_than_iso: str) -> list[Session]:
        """Active sessions whose last activity predates the cutoff (for auto-nudge)."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE state = %s AND last_activity_at < %s",
                (SESSION_ACTIVE, older_than_iso),
            ).fetchall()
        return [self._hydrate_session(r) for r in rows]

    # ── Players / roster ────────────────────────────────────────────────
    def replace_roster(self, session_id: int, players: list[Player]) -> None:
        """Overwrite the roster (used at setup / re-parse). Preserves session id only."""
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM players WHERE session_id = %s", (session_id,))
                for p in players:
                    conn.execute(
                        "INSERT INTO players "
                        "(session_id, side, number, name, position, is_target) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (session_id, p.side, p.number, p.name, p.position, p.is_target),
                    )

    def add_player(self, player: Player) -> Player:
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO players (session_id, side, number, name, position, is_target) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    player.session_id,
                    player.side,
                    player.number,
                    player.name,
                    player.position,
                    player.is_target,
                ),
            ).fetchone()
        player.id = row["id"]
        return player

    def set_target(self, player_id: int, is_target: bool) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE players SET is_target = %s WHERE id = %s",
                (is_target, player_id),
            )

    def list_players(self, session_id: int) -> list[Player]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM players WHERE session_id = %s ORDER BY side, number",
                (session_id,),
            ).fetchall()
        return [self._row_to_player(r) for r in rows]

    # ── Observations ────────────────────────────────────────────────────
    def add_observation(self, obs: Observation) -> Observation:
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO observations "
                "(session_id, player_id, side, sentiment, skill_category, raw_quote, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    obs.session_id,
                    obs.player_id,
                    obs.side,
                    obs.sentiment,
                    obs.skill_category,
                    obs.raw_quote,
                    obs.created_at or _now(),
                ),
            ).fetchone()
        obs.id = row["id"]
        self.touch_session(obs.session_id)
        return obs

    def last_observation(self, session_id: int) -> Observation | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM observations WHERE session_id = %s ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return self._row_to_obs(row) if row else None

    def delete_observation(self, obs_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM observations WHERE id = %s", (obs_id,))

    def update_observation(
        self,
        obs_id: int,
        *,
        player_id: int | None = None,
        side: str | None = None,
        sentiment: str | None = None,
        skill_category: str | None = None,
    ) -> None:
        sets, vals = [], []
        for col, val in (
            ("player_id", player_id),
            ("side", side),
            ("sentiment", sentiment),
            ("skill_category", skill_category),
        ):
            if val is not None:
                sets.append(f"{col} = %s")
                vals.append(val)
        if not sets:
            return
        vals.append(obs_id)
        with self._pool.connection() as conn:
            conn.execute(
                f"UPDATE observations SET {', '.join(sets)} WHERE id = %s", vals
            )

    def list_observations(self, session_id: int) -> list[Observation]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM observations WHERE session_id = %s ORDER BY id",
                (session_id,),
            ).fetchall()
        return [self._row_to_obs(r) for r in rows]

    # ── Hydration helpers ───────────────────────────────────────────────
    def _hydrate_session(self, row: dict) -> Session:
        session = Session(
            id=row["id"],
            agent_chat_id=row["agent_chat_id"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            label=row["label"],
            state=row["state"],
            roster_confirmed=bool(row["roster_confirmed"]),
            created_at=_iso(row["created_at"]),
            last_activity_at=_iso(row["last_activity_at"]),
            ended_at=_iso(row["ended_at"]),
        )
        session.players = self.list_players(session.id)
        session.observations = self.list_observations(session.id)
        return session

    @staticmethod
    def _row_to_player(row: dict) -> Player:
        return Player(
            id=row["id"],
            session_id=row["session_id"],
            side=row["side"],
            number=row["number"],
            name=row["name"],
            position=row["position"],
            is_target=bool(row["is_target"]),
        )

    @staticmethod
    def _row_to_obs(row: dict) -> Observation:
        return Observation(
            id=row["id"],
            session_id=row["session_id"],
            player_id=row["player_id"],
            side=row["side"],
            sentiment=row["sentiment"],
            skill_category=row["skill_category"],
            raw_quote=row["raw_quote"],
            created_at=_iso(row["created_at"]),
        )


def _iso(value) -> str | None:
    """Normalize a Postgres TIMESTAMPTZ (returned as datetime) back to the
    ISO-8601 string the rest of the app expects. Pass through None and strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(value)
