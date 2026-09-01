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

from .categories import split_category
from .models import (
    SESSION_ACTIVE,
    SESSION_ENDED,
    Observation,
    Prospect,
    ScoutProfile,
    Session,
)
from .taxonomy import name_matches, normalize_identity, normalize_name


# Optional bio a merge copies from the dropped prospect onto the survivor when
# the survivor has none. Identity columns (name/team and their normalized keys)
# are deliberately absent: the survivor's identity is the one being kept.
MERGEABLE_BIO_FIELDS = (
    "category",
    "position",
    "age",
    "birth_year",
    "height_cm",
    "weight_kg",
    "preferred_foot",
    "shirt_number",
    "nationality",
    "origin_club",
    "agent_name",
    "agent_phone",
    "market_value_usd",
    "contract_year",
    "photo_file_id",
    "notes",
    "latest_rating",
    "decision_status",
)


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
        # "Santa Fe U18" is a club and a category in one string: store the club
        # as the team (so it matches what prospects are keyed on) and the
        # category beside it. `metadata["category"]`, typed by the scout on
        # /nuevo, is a different field and is passed through untouched.
        home, home_category = split_category(home_team)
        away, away_category = split_category(away_team)
        session = await Session.create(
            agent_chat_id=agent_chat_id,
            home_team=home,
            away_team=away,
            home_team_category=home_category,
            away_team_category=away_category,
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
        """Stable prospect, keyed by (chat, identity-normalized name, team).

        Different shirt numbers across matches still resolve to one prospect, and
        a lightly-embellished name ("Castro B.") keys to the same prospect as the
        bare surname ("Castro") — see taxonomy.normalize_identity.

        The team is split into club + category first ("Santa Fe U18" → "Santa Fe"
        / "Sub-18") and the lookup runs on the club, so both spellings resolve to
        the same player instead of creating two records.
        """
        team, category = split_category(team)
        norm_name = normalize_identity(name)
        norm_team = normalize_name(team) if team else ""
        prospect = await Prospect.filter(
            agent_chat_id=chat_id,
            normalized_name=norm_name,
            normalized_team=norm_team,
        ).first()
        # No team stated and no exact teamless record → reuse the unique same-name
        # prospect if there's exactly one (e.g. "Ferrin" after "entra Ferrin de
        # Millonarios"). Don't guess a team when several same-name players exist.
        if prospect is None and not team:
            same_name = await Prospect.filter(
                agent_chat_id=chat_id, normalized_name=norm_name, is_temporary=False
            )
            named = [p for p in same_name if p.name]
            if len(named) == 1:
                prospect = named[0]
        if prospect is not None:
            # Backfill team/position if we now know them.
            changed = False
            if team and not prospect.team:
                prospect.team, prospect.normalized_team = team, norm_team
                changed = True
            if category and not prospect.category:
                prospect.category = category
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
            category=category,
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
        team, category = split_category(team)
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
            category=category,
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
        """Repoint the dropped prospect's observations onto the kept one, delete it.

        Bio the survivor is missing is backfilled from the record being dropped,
        so merging two halves of one player keeps every detail either half had.
        Only blanks are filled — the survivor's own values always win.
        """
        if keep_id == drop_id:
            return
        keep = await Prospect.filter(id=keep_id).first()
        drop = await Prospect.filter(id=drop_id).first()
        if keep is not None and drop is not None:
            backfill = {
                f: getattr(drop, f)
                for f in MERGEABLE_BIO_FIELDS
                if getattr(keep, f) is None and getattr(drop, f) is not None
            }
            if backfill:
                await Prospect.filter(id=keep_id).update(**backfill)
        await Observation.filter(prospect_id=drop_id).update(prospect_id=keep_id)
        await Prospect.filter(id=drop_id).delete()

    async def prospects_in_session(self, session_id: int) -> list[Prospect]:
        """Distinct named (non-temporary) prospects referenced by this session's
        observations — the dedup candidates at /finalizar."""
        rows = await Observation.filter(
            session_id=session_id, prospect_id__not_isnull=True
        ).values_list("prospect_id", flat=True)
        ids = {pid for pid in rows if pid is not None}
        if not ids:
            return []
        prospects = await Prospect.filter(
            id__in=ids, is_temporary=False
        ).exclude(name="").order_by("id")
        return list(prospects)

    async def all_prospects(self, chat_id: int) -> list[Prospect]:
        """Every named (non-temporary) prospect for a chat, oldest first — the
        rows of the cumulative historical report."""
        rows = (
            await Prospect.filter(agent_chat_id=chat_id, is_temporary=False)
            .exclude(name="")
            .order_by("id")
        )
        return list(rows)

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
