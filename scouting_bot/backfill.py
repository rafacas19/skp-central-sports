"""One-off backfill: split stored team names into club + category.

Everything written from now on is split at capture time (see
`categories.split_category`, wired into `storage` and the dashboard form), but
rows written before that still carry the category inside the team name. This
module plans and applies that catch-up pass over existing data.

Two things happen per row:

  * the team string loses its category ("Santa Fe U18" → "Santa Fe") and the
    category lands in its own column;
  * because prospect identity keys on the normalized team, stripping can make
    two records collide — "Pérez / Santa Fe U18" and "Pérez / Santa Fe" become
    the same player. Those are **merged**, keeping the oldest id (the same
    direction `Storage.merge_prospects` implements for the bot's dedup flow and
    the dashboard merge page).

The plan is computed first and can be printed without touching anything, which
is what the CLI does by default — the merges are irreversible, so they are shown
before they happen. Re-running is safe: an already-split name derives nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .categories import split_category
from .models import Prospect, Session
from .storage import Storage
from .taxonomy import normalize_name


@dataclass
class SessionChange:
    """A session whose home and/or away team name carries a category."""

    id: int
    home_team: str
    new_home_team: str
    home_category: str | None
    away_team: str
    new_away_team: str
    away_category: str | None

    @property
    def changed(self) -> bool:
        return self.home_category is not None or self.away_category is not None


@dataclass
class ProspectChange:
    """A prospect whose team name carries a category.

    `merge_into` is the id of the record this one collapses onto once the team is
    stripped (None when it stays its own record)."""

    id: int
    name: str
    team: str
    new_team: str
    category: str | None
    merge_into: int | None = None


@dataclass
class Plan:
    sessions: list[SessionChange] = field(default_factory=list)
    prospects: list[ProspectChange] = field(default_factory=list)
    active_sessions: int = 0

    @property
    def merges(self) -> list[ProspectChange]:
        return [p for p in self.prospects if p.merge_into is not None]

    @property
    def empty(self) -> bool:
        return not self.sessions and not self.prospects


async def plan_backfill() -> Plan:
    """What the backfill would do, without doing any of it."""
    plan = Plan()

    for s in await Session.all().order_by("id"):
        home, home_category = split_category(s.home_team)
        away, away_category = split_category(s.away_team)
        if home_category is None and away_category is None:
            continue
        plan.sessions.append(
            SessionChange(
                id=s.id,
                home_team=s.home_team, new_home_team=home, home_category=home_category,
                away_team=s.away_team, new_away_team=away, away_category=away_category,
            )
        )

    prospects = await Prospect.all().order_by("id")
    # Identity after stripping, so collisions are visible before any write. The
    # first id seen for a key is the oldest (ordered by id) and survives.
    survivor: dict[tuple[int, str, str], int] = {}
    for p in prospects:
        club, category = split_category(p.team)
        key = (p.agent_chat_id, p.normalized_name or "", normalize_name(club or ""))
        keeper = survivor.setdefault(key, p.id)
        if category is None and keeper == p.id:
            continue  # nothing to strip and nobody to merge with
        plan.prospects.append(
            ProspectChange(
                id=p.id,
                name=p.name or "(sin nombre)",
                team=p.team or "",
                new_team=club or "",
                category=category,
                merge_into=None if keeper == p.id else keeper,
            )
        )

    plan.active_sessions = await Session.filter(state="active").count()
    return plan


async def apply_backfill(plan: Plan) -> None:
    """Execute a plan. Merges run last so the surviving record already carries
    its stripped team and category when the others fold into it."""
    for change in plan.sessions:
        await Session.filter(id=change.id).update(
            home_team=change.new_home_team,
            home_team_category=change.home_category,
            away_team=change.new_away_team,
            away_team_category=change.away_category,
        )

    for change in plan.prospects:
        updates = {
            "team": change.new_team or None,
            "normalized_team": normalize_name(change.new_team or ""),
        }
        if change.category:
            updates["category"] = change.category
        await Prospect.filter(id=change.id).update(**updates)

    storage = Storage()
    for change in plan.merges:
        # Bio the survivor lacks (the category included) is carried over, the
        # dropped record's observations are repointed, and the row is deleted.
        await storage.merge_prospects(change.merge_into, change.id)


def format_plan(plan: Plan) -> str:
    """The plan as human-readable lines — what the CLI prints in dry-run."""
    lines: list[str] = []

    lines.append(f"Partidos por actualizar: {len(plan.sessions)}")
    for c in plan.sessions:
        if c.home_category:
            lines.append(f"  #{c.id} local     «{c.home_team}» → «{c.new_home_team}» + {c.home_category}")
        if c.away_category:
            lines.append(f"  #{c.id} visitante «{c.away_team}» → «{c.new_away_team}» + {c.away_category}")

    lines.append(f"Jugadores por actualizar: {len(plan.prospects)} "
                 f"(de los cuales {len(plan.merges)} se fusionan)")
    for c in plan.prospects:
        suffix = f"  ⚠ se fusiona con el jugador #{c.merge_into}" if c.merge_into else ""
        category = f" + {c.category}" if c.category else ""
        lines.append(f"  #{c.id} {c.name}: «{c.team}» → «{c.new_team}»{category}{suffix}")

    if plan.empty:
        lines.append("Nada que hacer: ningún nombre de equipo lleva categoría.")
    return "\n".join(lines)
