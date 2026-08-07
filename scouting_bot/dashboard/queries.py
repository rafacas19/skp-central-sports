"""Read-only aggregate queries for the dashboard pages.

The dataset is small (tens of sessions, low hundreds of observations), so these
favor clarity over query golf: fetch with prefetch and aggregate in Python.
Everything is scoped per scout (`agent_chat_id`) implicitly by NOT filtering —
today there is a single scout; when a second one joins, add a chat-id filter
parameter here and nowhere else.
"""

from __future__ import annotations

from ..models import (
    RATING_DECISIONS,
    SESSION_ACTIVE,
    Prospect,
    Session,
    decision_for_rating,
)

# Canonical display order for the decision strip, best first.
DECISION_ORDER = [RATING_DECISIONS[r] for r in (5, 4, 3, 2, 1)]
NO_DECISION = "Sin valorar"


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
