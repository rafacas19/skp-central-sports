"""Persistence layer over Tortoise ORM (async).

This is the single place that issues database queries, so the service and report
layers stay storage-agnostic. Sessions are returned with `players` and
`observations` prefetched, letting `report.py` and the disambiguation logic read
them as plain lists without awaiting.

Resilient sessions: state is always in Postgres, so a restart or a dropped phone
never loses an active session — the agent just keeps sending notes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    SESSION_ACTIVE,
    SESSION_ENDED,
    Observation,
    Player,
    Session,
)


def _now() -> datetime:
    """Timezone-aware UTC now (Tortoise DatetimeField stores tz-aware values)."""
    return datetime.now(timezone.utc)


class Storage:
    """Async repository. Stateless — safe to share a single instance."""

    # ── Sessions ────────────────────────────────────────────────────────
    async def create_session(
        self, agent_chat_id: int, home_team: str, away_team: str, label: str | None
    ) -> Session:
        session = await Session.create(
            agent_chat_id=agent_chat_id,
            home_team=home_team,
            away_team=away_team,
            label=label,
            state=SESSION_ACTIVE,
            roster_confirmed=False,
        )
        return await self.get_session(session.id)

    async def get_active_session(self, agent_chat_id: int) -> Session | None:
        session = (
            await Session.filter(agent_chat_id=agent_chat_id, state=SESSION_ACTIVE)
            .order_by("-id")
            .first()
        )
        if session is None:
            return None
        return await self.get_session(session.id)

    async def get_session(self, session_id: int) -> Session:
        session = (
            await Session.filter(id=session_id)
            .prefetch_related("players", "observations")
            .first()
        )
        if session is None:
            raise KeyError(f"session {session_id} not found")
        return session

    async def touch_session(self, session_id: int) -> None:
        await Session.filter(id=session_id).update(last_activity_at=_now())

    async def confirm_roster(self, session_id: int) -> None:
        await Session.filter(id=session_id).update(
            roster_confirmed=True, last_activity_at=_now()
        )

    async def end_session(self, session_id: int) -> None:
        ts = _now()
        await Session.filter(id=session_id).update(
            state=SESSION_ENDED, ended_at=ts, last_activity_at=ts
        )

    async def stale_active_sessions(self, older_than: datetime) -> list[Session]:
        """Active sessions whose last activity predates the cutoff (auto-nudge)."""
        sessions = await Session.filter(
            state=SESSION_ACTIVE, last_activity_at__lt=older_than
        ).prefetch_related("players", "observations")
        return list(sessions)

    # ── Players / roster ────────────────────────────────────────────────
    async def replace_roster(self, session_id: int, players: list[Player]) -> None:
        """Overwrite the roster (setup / re-parse). Preserves session id only."""
        await Player.filter(session_id=session_id).delete()
        await Player.bulk_create(
            [
                Player(
                    session_id=session_id,
                    side=p.side,
                    number=p.number,
                    name=p.name,
                    position=p.position,
                    is_target=p.is_target,
                )
                for p in players
            ]
        )

    async def add_player(self, player: Player) -> Player:
        await player.save()
        return player

    async def set_target(self, player_id: int, is_target: bool) -> None:
        await Player.filter(id=player_id).update(is_target=is_target)

    async def list_players(self, session_id: int) -> list[Player]:
        players = await Player.filter(session_id=session_id).order_by("side", "number")
        return list(players)

    # ── Observations ────────────────────────────────────────────────────
    async def add_observation(self, obs: Observation) -> Observation:
        await obs.save()
        await self.touch_session(obs.session_id)
        return obs

    async def last_observation(self, session_id: int) -> Observation | None:
        return (
            await Observation.filter(session_id=session_id).order_by("-id").first()
        )

    async def delete_observation(self, obs_id: int) -> None:
        await Observation.filter(id=obs_id).delete()

    async def update_observation(
        self,
        obs_id: int,
        *,
        player_id: int | None = None,
        side: str | None = None,
        sentiment: str | None = None,
        skill_category: str | None = None,
    ) -> None:
        updates: dict = {}
        if player_id is not None:
            updates["player_id"] = player_id
        if side is not None:
            updates["side"] = side
        if sentiment is not None:
            updates["sentiment"] = sentiment
        if skill_category is not None:
            updates["skill_category"] = skill_category
        if not updates:
            return
        await Observation.filter(id=obs_id).update(**updates)

    async def list_observations(self, session_id: int) -> list[Observation]:
        observations = await Observation.filter(session_id=session_id).order_by("id")
        return list(observations)
