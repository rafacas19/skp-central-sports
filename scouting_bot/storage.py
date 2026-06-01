"""SQLite persistence layer.

Chosen for the MVP because it is zero-setup and matches the per-match data scope.
The repository functions are the only place that touches SQL, so swapping to
Postgres in Phase 2 (the cross-match recruitment DB) is contained here.

Resilient sessions: state is always on disk, so a bot restart or an agent's
phone dying never loses an active session — the agent just keeps sending notes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .models import (
    HOME,
    SESSION_ACTIVE,
    SESSION_ENDED,
    Observation,
    Player,
    Session,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_chat_id     INTEGER NOT NULL,
    home_team         TEXT NOT NULL,
    away_team         TEXT NOT NULL,
    label             TEXT,
    state             TEXT NOT NULL DEFAULT 'active',
    roster_confirmed  INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    last_activity_at  TEXT NOT NULL,
    ended_at          TEXT
);

CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    side        TEXT NOT NULL,
    number      INTEGER,
    name        TEXT NOT NULL,
    position    TEXT,
    is_target   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    player_id       INTEGER REFERENCES players(id) ON DELETE SET NULL,
    side            TEXT,
    sentiment       TEXT,
    skill_category  TEXT,
    raw_quote       TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- At most one active session per agent (enforced in app logic + this index helps lookups).
CREATE INDEX IF NOT EXISTS idx_sessions_agent_state ON sessions(agent_chat_id, state);
CREATE INDEX IF NOT EXISTS idx_players_session ON players(session_id);
CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Sessions ────────────────────────────────────────────────────────
    def create_session(
        self, agent_chat_id: int, home_team: str, away_team: str, label: str | None
    ) -> Session:
        ts = _now()
        cur = self._conn.execute(
            """INSERT INTO sessions
               (agent_chat_id, home_team, away_team, label, state,
                roster_confirmed, created_at, last_activity_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (agent_chat_id, home_team, away_team, label, SESSION_ACTIVE, ts, ts),
        )
        self._conn.commit()
        return self.get_session(cur.lastrowid)  # type: ignore[arg-type]

    def get_active_session(self, agent_chat_id: int) -> Session | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE agent_chat_id = ? AND state = ? "
            "ORDER BY id DESC LIMIT 1",
            (agent_chat_id, SESSION_ACTIVE),
        ).fetchone()
        return self._hydrate_session(row) if row else None

    def get_session(self, session_id: int) -> Session:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"session {session_id} not found")
        return self._hydrate_session(row)

    def touch_session(self, session_id: int) -> None:
        self._conn.execute(
            "UPDATE sessions SET last_activity_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        self._conn.commit()

    def confirm_roster(self, session_id: int) -> None:
        self._conn.execute(
            "UPDATE sessions SET roster_confirmed = 1, last_activity_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        self._conn.commit()

    def end_session(self, session_id: int) -> None:
        ts = _now()
        self._conn.execute(
            "UPDATE sessions SET state = ?, ended_at = ?, last_activity_at = ? WHERE id = ?",
            (SESSION_ENDED, ts, ts, session_id),
        )
        self._conn.commit()

    def stale_active_sessions(self, older_than_iso: str) -> list[Session]:
        """Active sessions whose last activity predates the cutoff (for auto-nudge)."""
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE state = ? AND last_activity_at < ?",
            (SESSION_ACTIVE, older_than_iso),
        ).fetchall()
        return [self._hydrate_session(r) for r in rows]

    # ── Players / roster ────────────────────────────────────────────────
    def replace_roster(self, session_id: int, players: list[Player]) -> None:
        """Overwrite the roster (used at setup / re-parse). Preserves session id only."""
        self._conn.execute("DELETE FROM players WHERE session_id = ?", (session_id,))
        for p in players:
            self._conn.execute(
                "INSERT INTO players (session_id, side, number, name, position, is_target) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, p.side, p.number, p.name, p.position, int(p.is_target)),
            )
        self._conn.commit()

    def add_player(self, player: Player) -> Player:
        cur = self._conn.execute(
            "INSERT INTO players (session_id, side, number, name, position, is_target) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                player.session_id,
                player.side,
                player.number,
                player.name,
                player.position,
                int(player.is_target),
            ),
        )
        self._conn.commit()
        player.id = cur.lastrowid
        return player

    def set_target(self, player_id: int, is_target: bool) -> None:
        self._conn.execute(
            "UPDATE players SET is_target = ? WHERE id = ?",
            (int(is_target), player_id),
        )
        self._conn.commit()

    def list_players(self, session_id: int) -> list[Player]:
        rows = self._conn.execute(
            "SELECT * FROM players WHERE session_id = ? ORDER BY side, number", (session_id,)
        ).fetchall()
        return [self._row_to_player(r) for r in rows]

    # ── Observations ────────────────────────────────────────────────────
    def add_observation(self, obs: Observation) -> Observation:
        cur = self._conn.execute(
            "INSERT INTO observations "
            "(session_id, player_id, side, sentiment, skill_category, raw_quote, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                obs.session_id,
                obs.player_id,
                obs.side,
                obs.sentiment,
                obs.skill_category,
                obs.raw_quote,
                obs.created_at or _now(),
            ),
        )
        self._conn.commit()
        obs.id = cur.lastrowid
        self.touch_session(obs.session_id)
        return obs

    def last_observation(self, session_id: int) -> Observation | None:
        row = self._conn.execute(
            "SELECT * FROM observations WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return self._row_to_obs(row) if row else None

    def delete_observation(self, obs_id: int) -> None:
        self._conn.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
        self._conn.commit()

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
                sets.append(f"{col} = ?")
                vals.append(val)
        if not sets:
            return
        vals.append(obs_id)
        self._conn.execute(
            f"UPDATE observations SET {', '.join(sets)} WHERE id = ?", vals
        )
        self._conn.commit()

    def list_observations(self, session_id: int) -> list[Observation]:
        rows = self._conn.execute(
            "SELECT * FROM observations WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        return [self._row_to_obs(r) for r in rows]

    # ── Hydration helpers ───────────────────────────────────────────────
    def _hydrate_session(self, row: sqlite3.Row) -> Session:
        session = Session(
            id=row["id"],
            agent_chat_id=row["agent_chat_id"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            label=row["label"],
            state=row["state"],
            roster_confirmed=bool(row["roster_confirmed"]),
            created_at=row["created_at"],
            last_activity_at=row["last_activity_at"],
            ended_at=row["ended_at"],
        )
        session.players = self.list_players(session.id)
        session.observations = self.list_observations(session.id)
        return session

    @staticmethod
    def _row_to_player(row: sqlite3.Row) -> Player:
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
    def _row_to_obs(row: sqlite3.Row) -> Observation:
        return Observation(
            id=row["id"],
            session_id=row["session_id"],
            player_id=row["player_id"],
            side=row["side"],
            sentiment=row["sentiment"],
            skill_category=row["skill_category"],
            raw_quote=row["raw_quote"],
            created_at=row["created_at"],
        )
