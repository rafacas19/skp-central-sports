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
    Prospect,
    ScoutProfile,
    Session,
)
from .taxonomy import name_matches, normalize_name


def _now() -> datetime:
    """Timezone-aware UTC now (Tortoise DatetimeField stores tz-aware values)."""
    return datetime.now(timezone.utc)


class Storage:
    """Async repository. Stateless — safe to share a single instance."""

    # ── Sessions ────────────────────────────────────────────────────────
    async def create_session(
        self,
        agent_chat_id: int,
        home_team: str,
        away_team: str,
        label: str | None,
        **metadata,
    ) -> Session:
        session = await Session.create(
            agent_chat_id=agent_chat_id,
            home_team=home_team,
            away_team=away_team,
            label=label,
            state=SESSION_ACTIVE,
            **metadata,  # scout_name / competition / category / location / match_date
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
            .prefetch_related("observations__prospect")
            .first()
        )
        if session is None:
            raise KeyError(f"session {session_id} not found")
        return session

    async def touch_session(self, session_id: int) -> None:
        await Session.filter(id=session_id).update(last_activity_at=_now())

    async def update_session_meta(self, session_id: int, **fields) -> None:
        if fields:
            await Session.filter(id=session_id).update(**fields)

    async def end_session(self, session_id: int) -> None:
        ts = _now()
        await Session.filter(id=session_id).update(
            state=SESSION_ENDED, ended_at=ts, last_activity_at=ts
        )

    async def stale_active_sessions(self, older_than: datetime) -> list[Session]:
        """Active sessions whose last activity predates the cutoff (auto-nudge)."""
        sessions = await Session.filter(
            state=SESSION_ACTIVE, last_activity_at__lt=older_than
        ).prefetch_related("observations")
        return list(sessions)

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

    async def list_observations(self, session_id: int) -> list[Observation]:
        observations = await Observation.filter(session_id=session_id).order_by("id")
        return list(observations)

    # ── Scout profile ───────────────────────────────────────────────────
    async def get_scout_name(self, chat_id: int) -> str | None:
        profile = await ScoutProfile.filter(agent_chat_id=chat_id).first()
        return profile.name if profile else None

    async def set_scout_name(self, chat_id: int, name: str) -> None:
        await ScoutProfile.update_or_create(
            agent_chat_id=chat_id, defaults={"name": name}
        )

    # ── Prospects (cross-match player identity) ─────────────────────────
    async def get_or_create_prospect(
        self,
        chat_id: int,
        name: str,
        team: str | None,
        *,
        position: str | None = None,
    ) -> Prospect:
        """Stable prospect, keyed by (chat, normalized name, normalized team).

        Different shirt numbers across matches still resolve to one prospect.
        """
        norm_name = normalize_name(name)
        norm_team = normalize_name(team) if team else ""
        prospect = await Prospect.filter(
            agent_chat_id=chat_id,
            normalized_name=norm_name,
            normalized_team=norm_team,
        ).first()
        if prospect is not None:
            # Backfill team/position if we now know them.
            changed = False
            if team and not prospect.team:
                prospect.team, prospect.normalized_team = team, norm_team
                changed = True
            if position and not prospect.position:
                prospect.position = position
                changed = True
            if changed:
                await prospect.save()
            return prospect
        prospect = await Prospect.create(
            agent_chat_id=chat_id,
            name=name,
            normalized_name=norm_name,
            team=team,
            normalized_team=norm_team,
            position=position,
            is_temporary=not name,  # a blank name ⇒ temporary
        )
        return prospect

    async def get_or_create_temp_prospect(
        self, chat_id: int, session_id: int, team: str | None, number: int | None
    ) -> Prospect:
        """A match-scoped temporary prospect for a number-only / unknown player.

        Keyed within the match by (team, number) so repeated number-only notes in
        the same match reuse one record. Marked temporary until the scout names it.
        """
        label = team or "?"
        marker = f"#{number}" if number is not None else "?"
        synthetic = f"__temp__:{session_id}:{normalize_name(label)}:{marker}"
        prospect = await Prospect.filter(
            agent_chat_id=chat_id, normalized_name=synthetic
        ).first()
        if prospect is not None:
            return prospect
        return await Prospect.create(
            agent_chat_id=chat_id,
            name="",
            normalized_name=synthetic,
            team=team,
            normalized_team=normalize_name(team) if team else "",
            is_temporary=True,
        )

    async def get_prospect(self, prospect_id: int) -> Prospect | None:
        return await Prospect.filter(id=prospect_id).first()

    async def find_prospects_by_name(self, chat_id: int, name: str) -> list[Prospect]:
        """Named (non-temporary) prospects whose name fuzzily matches `name`."""
        owned = await Prospect.filter(agent_chat_id=chat_id, is_temporary=False)
        return [p for p in owned if p.name and name_matches(name, p.name)]

    async def recent_temp_prospects(self, chat_id: int) -> list[Prospect]:
        """The most recent temporary (unknown) prospect, for /editar to name it."""
        rows = (
            await Prospect.filter(agent_chat_id=chat_id, is_temporary=True)
            .order_by("-id")
            .limit(1)
        )
        return list(rows)

    async def update_prospect(self, prospect_id: int, **fields) -> None:
        if not fields:
            return
        await Prospect.filter(id=prospect_id).update(**fields)

    async def merge_prospects(self, keep_id: int, drop_id: int) -> None:
        """Repoint the dropped prospect's observations onto the kept one, delete it."""
        if keep_id == drop_id:
            return
        await Observation.filter(prospect_id=drop_id).update(prospect_id=keep_id)
        await Prospect.filter(id=drop_id).delete()

    async def observations_for_prospect(
        self, chat_id: int, prospect_id: int
    ) -> list[Observation]:
        """All observations for a prospect across matches, oldest first, with the
        session prefetched (for Match/Date/Opponent in cross-match reports)."""
        observations = (
            await Observation.filter(
                prospect_id=prospect_id, session__agent_chat_id=chat_id
            )
            .prefetch_related("session")
            .order_by("created_at", "id")
        )
        return list(observations)
