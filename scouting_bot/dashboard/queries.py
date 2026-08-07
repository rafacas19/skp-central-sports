"""Read-only aggregate queries for the dashboard pages.

The dataset is small (tens of sessions, low hundreds of observations), so these
favor clarity over query golf: fetch with prefetch and aggregate in Python.
Everything is scoped per scout (`agent_chat_id`) implicitly by NOT filtering —
today there is a single scout; when a second one joins, add a chat-id filter
parameter here and nowhere else.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from ..models import (
    RATING_DECISIONS,
    SESSION_ACTIVE,
    Observation,
    Prospect,
    Session,
    decision_for_rating,
)

# Canonical display order for the decision strip, best first.
DECISION_ORDER = [RATING_DECISIONS[r] for r in (5, 4, 3, 2, 1)]
NO_DECISION = "Sin valorar"

# The scout works in Colombia; timestamps are stored UTC. All display (and the
# date filters, so what you see is what you filter) uses this timezone.
TZ = ZoneInfo("America/Bogota")


def _local_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TZ).date()


def prospect_decision(p: Prospect) -> str | None:
    """The client-facing decision, same precedence as report.py: an explicit
    /decision status wins, otherwise it derives from the latest 1–5 rating."""
    return p.decision_status or decision_for_rating(p.latest_rating)


def display_name(p: Prospect) -> str:
    """Human label for a prospect; temporary/number-only profiles get a
    descriptive placeholder instead of an empty string (never drop them)."""
    if p.name:
        return p.name
    number = _first_number(p)
    if number is not None:
        return f"Sin identificar (dorsal {number})"
    return "Sin identificar"


def _first_number(p: Prospect) -> int | None:
    for o in getattr(p, "observations", []) or []:
        if o.player_number is not None:
            return o.player_number
    return None


async def overview() -> dict:
    """Everything the overview page shows, in one shot."""
    sessions = await Session.all().prefetch_related("observations").order_by("-created_at")
    prospects = await Prospect.all().prefetch_related("observations")

    total_observations = sum(len(s.observations) for s in sessions)
    team_notes = sum(1 for s in sessions for o in s.observations if o.is_team_note)

    temporary = [p for p in prospects if p.is_temporary or not p.name]
    rated = [p for p in prospects if p.latest_rating is not None]

    # Decision strip: canonical order first, any legacy statuses after,
    # unrated last.
    counts: dict[str, int] = {}
    for p in prospects:
        label = prospect_decision(p) or NO_DECISION
        counts[label] = counts.get(label, 0) + 1
    ordered = [d for d in DECISION_ORDER if d in counts]
    ordered += sorted(d for d in counts if d not in DECISION_ORDER and d != NO_DECISION)
    if NO_DECISION in counts:
        ordered.append(NO_DECISION)
    decisions = [{"label": d, "count": counts[d]} for d in ordered]

    recent = [
        {
            "id": s.id,
            "home_team": s.home_team,
            "away_team": s.away_team,
            "competition": s.competition,
            "date": s.match_date or s.created_at,
            "is_active": s.state == SESSION_ACTIVE,
            "players": len({o.prospect_id for o in s.observations if o.prospect_id}),
            "observations": len(s.observations),
        }
        for s in sessions[:5]
    ]

    top = sorted(rated, key=lambda p: (-p.latest_rating, p.name or "~"))[:10]
    top_players = [
        {
            "id": p.id,
            "name": display_name(p),
            "team": p.team,
            "rating": p.latest_rating,
            "decision": prospect_decision(p),
            "matches": len({o.session_id for o in p.observations}),
        }
        for p in top
    ]

    return {
        "totals": {
            "sessions": len(sessions),
            "active_sessions": sum(1 for s in sessions if s.state == SESSION_ACTIVE),
            "prospects": len(prospects),
            "temporary_prospects": len(temporary),
            "observations": total_observations,
            "team_notes": team_notes,
            "rated_prospects": len(rated),
        },
        "decisions": decisions,
        "recent_sessions": recent,
        "top_players": top_players,
    }


async def list_matches(
    *,
    competition: str | None = None,
    state: str | None = None,  # "activo" | "finalizado" | None (todos)
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """All matches newest-first, filtered in Python (tens of rows). Also returns
    the distinct competitions so the filter dropdown reflects real data."""
    sessions = await Session.all().prefetch_related("observations").order_by("-created_at")
    competitions = sorted({s.competition for s in sessions if s.competition})

    rows = []
    for s in sessions:
        if competition and s.competition != competition:
            continue
        is_active = s.state == SESSION_ACTIVE
        if state == "activo" and not is_active:
            continue
        if state == "finalizado" and is_active:
            continue
        day = _local_date(s.match_date or s.created_at)
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        rows.append(
            {
                "id": s.id,
                "home_team": s.home_team,
                "away_team": s.away_team,
                "date": s.match_date or s.created_at,
                "competition": s.competition,
                "location": s.location,
                "scout_name": s.scout_name,
                "is_active": is_active,
                "players": len({o.prospect_id for o in s.observations if o.prospect_id}),
                "observations": len(s.observations),
            }
        )
    return {"matches": rows, "competitions": competitions}


async def match_detail(session_id: int) -> dict | None:
    """One match: metadata header + the full annotation timeline."""
    s = await Session.get_or_none(id=session_id)
    if s is None:
        return None
    # created_at is the true chronology (notes are captured live); the stored
    # minute is display metadata and can jump backwards after a clock resync.
    obs = await Observation.filter(session_id=session_id).order_by("created_at", "id")

    timeline = [
        {
            "minute": o.minute,
            "player_name": o.player_name,
            "player_number": o.player_number,
            "prospect_id": o.prospect_id,
            "team": o.team,
            "quote": o.raw_quote,
            "rating": o.rating,
            "is_substitution": o.is_substitution,
            "source": o.source,
        }
        for o in obs
        if not o.is_team_note
    ]
    team_notes = [
        {"team": o.team, "quote": o.raw_quote, "minute": o.minute}
        for o in obs
        if o.is_team_note
    ]

    return {
        "match": {
            "id": s.id,
            "home_team": s.home_team,
            "away_team": s.away_team,
            "date": s.match_date or s.created_at,
            "competition": s.competition,
            "category": s.category,
            "location": s.location,
            "scout_name": s.scout_name,
            "is_active": s.state == SESSION_ACTIVE,
            "first_half_started_at": s.first_half_started_at,
            "second_half_started_at": s.second_half_started_at,
            "ended_at": s.ended_at,
            "players": len({o.prospect_id for o in obs if o.prospect_id}),
            "observations": len(obs),  # all annotations, matching the list column
        },
        "timeline": timeline,
        "team_notes": team_notes,
    }
