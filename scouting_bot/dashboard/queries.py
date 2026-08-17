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
    DECISION_ADVANCE,
    RATING_DECISIONS,
    SESSION_ACTIVE,
    Observation,
    Prospect,
    Session,
    current_age,
    decision_for_rating,
)
from ..positions import (
    CATEGORIES,
    canonical_position,
    category_index,
    position_abbr,
    position_category,
)
from ..taxonomy import name_matches, normalize_name

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


def _ordered_labels(present: set[str]) -> list[str]:
    """Decision labels in display order: canonical best-first, then any legacy
    manual statuses alphabetically, unrated last."""
    ordered = [d for d in DECISION_ORDER if d in present]
    ordered += sorted(present - set(DECISION_ORDER) - {NO_DECISION})
    if NO_DECISION in present:
        ordered.append(NO_DECISION)
    return ordered


def _initials(p: Prospect) -> str:
    """Up to two initials for the photo placeholder; a dorsal when unnamed."""
    if p.name:
        parts = [w for w in p.name.split() if w]
        return "".join(w[0] for w in parts[:2]).upper()
    number = _first_number(p)
    return f"#{number}" if number is not None else "?"


def _player_row(p: Prospect) -> dict:
    """The list/card representation of a prospect (observations prefetched).

    `position` stays the scout's own words; `position_abbr`/`position_category`
    are the derived taxonomy used for badges, grouping and filtering.
    `trend` is filled in by `_add_trends` where match ordering is available."""
    matches = len({o.session_id for o in p.observations})
    return {
        "id": p.id,
        "name": display_name(p),
        "is_temporary": p.is_temporary or not p.name,
        "team": p.team,
        "position": p.position,
        "position_abbr": position_abbr(p.position),
        "position_category": position_category(p.position),
        "age": current_age(p.birth_year, p.age),
        "foot": p.preferred_foot,
        "nationality": p.nationality,
        "market_value_usd": p.market_value_usd,
        "rating": p.latest_rating,
        "decision": prospect_decision(p),
        "matches": matches,
        "observations": len(p.observations),
        # Card presentation: how the player is shown, and how much to trust the
        # rating (a single viewing is a weaker claim than four).
        "initials": _initials(p),
        "has_photo": bool(p.photo_file_id),
        "single_match": matches <= 1,
        "trend": None,
    }


def _rows_sorted(rows: list[dict]) -> list[dict]:
    """Best-rated first, then named players A-Z, temps last."""
    return sorted(
        rows,
        key=lambda r: (
            r["rating"] is None,
            -(r["rating"] or 0),
            r["is_temporary"],
            r["name"],
        ),
    )


# Decisions worth putting on the home screen, best first. Everything below these
# is a count and a link, not a card — the landing page is for players the scout
# should act on. Mixes the rating-derived ladder with DECISION_ADVANCE: a scout's
# deliberate "move this player forward" call via the legacy /decision command is
# as actionable as a top rating, even without one. The other three legacy
# statuses stay off the home screen: Pendiente is "no decision yet" (nothing to
# act on), Seguir observando sits at the same watch-list tier as the rating-2
# "A seguir" (also not featured), and Descartar is negative.
FEATURED_DECISIONS = [RATING_DECISIONS[5], DECISION_ADVANCE, RATING_DECISIONS[4], RATING_DECISIONS[3]]


def _rating_trend(p: Prospect, sessions: dict[int, Session]) -> str | None:
    """Whether the player's rating went up, down or held since the match before.

    Ratings are averaged per match (a scout may rate the same player twice in
    one game) and compared across the two most recent rated matches. None when
    there is nothing to compare against."""
    per_match: dict[int, list[float]] = {}
    for o in p.observations:
        if o.rating is not None:
            per_match.setdefault(o.session_id, []).append(o.rating)
    if len(per_match) < 2:
        return None

    def when(session_id: int):
        s = sessions.get(session_id)
        return (s.match_date or s.created_at) if s else None

    ordered = sorted(
        (sid for sid in per_match if when(sid) is not None), key=when, reverse=True
    )
    if len(ordered) < 2:
        return None
    latest = sum(per_match[ordered[0]]) / len(per_match[ordered[0]])
    previous = sum(per_match[ordered[1]]) / len(per_match[ordered[1]])
    if latest > previous:
        return "sube"
    if latest < previous:
        return "baja"
    return "igual"


async def _add_trends(prospects: list[Prospect], rows: list[dict]) -> None:
    """Fill in each row's rating trend, in place. Needs match dates, so it costs
    one extra read — only worth it on the pages that show cards."""
    sessions = {s.id: s for s in await Session.all()}
    for p, row in zip(prospects, rows):
        row["trend"] = _rating_trend(p, sessions)


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
    decisions = [{"label": d, "count": counts[d]} for d in _ordered_labels(set(counts))]

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

    # Featured tiers — the point of the home screen. Players the scout should
    # act on come first as cards, grouped by decision and best-rated within each
    # group; the rest stay as counts in the decision strip.
    by_session = {s.id: s for s in sessions}
    featured_rank = {label: i for i, label in enumerate(FEATURED_DECISIONS)}
    cards = []
    for p in prospects:
        if prospect_decision(p) not in featured_rank:
            continue
        row = _player_row(p)
        row["trend"] = _rating_trend(p, by_session)
        cards.append(row)
    # One list rather than a section per decision: these are all "act on this
    # player". The decision still leads the ordering — an explicit Avanzar
    # outranks a merely well-rated player — and each card carries its own label.
    # sorted() is stable, so the usual best-rated-first order survives within
    # each decision.
    featured = sorted(_rows_sorted(cards), key=lambda r: featured_rank[r["decision"]])

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
        "featured": featured,
        "featured_total": len(featured),
        "recent_sessions": recent,
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


# Age filters use football's own brackets rather than a slider: "sub-20" means
# 20 or younger, so the groups overlap the way a scout talks about them.
AGE_BUCKETS: tuple[tuple[str, str, int | None, int | None], ...] = (
    ("sub17", "Sub-17", None, 17),
    ("sub20", "Sub-20", None, 20),
    ("sub23", "Sub-23", None, 23),
    ("mayores", "Mayores de 23", 24, None),
)
_AGE_BY_KEY = {key: (low, high) for key, _, low, high in AGE_BUCKETS}

# How a filtered list is ordered. Rating first: it is the reason to look.
SORT_OPTIONS = (
    ("valoracion", "Mejor valorados"),
    ("edad", "Más jóvenes"),
    ("valor", "Mayor valor"),
    ("nombre", "Nombre (A-Z)"),
)


def _in_age_bucket(age: int | None, bucket: str | None) -> bool:
    if not bucket or bucket not in _AGE_BY_KEY:
        return True
    if age is None:
        return False  # an unknown age can't be claimed for a bracket
    low, high = _AGE_BY_KEY[bucket]
    return (low is None or age >= low) and (high is None or age <= high)


def _matches_position(row: dict, wanted: str | None) -> bool:
    """`wanted` is either a broad category ("Defensa") or a specific role."""
    if not wanted:
        return True
    if wanted in CATEGORIES:
        return row["position_category"] == wanted
    return normalize_name(row["position"] or "") == normalize_name(wanted)


def _sorted_by(rows: list[dict], sort: str | None) -> list[dict]:
    if sort == "edad":
        return sorted(rows, key=lambda r: (r["age"] is None, r["age"] or 0, r["name"]))
    if sort == "valor":
        return sorted(
            rows,
            key=lambda r: (
                r["market_value_usd"] is None,
                -(r["market_value_usd"] or 0),
                r["name"],
            ),
        )
    if sort == "nombre":
        return sorted(rows, key=lambda r: (r["is_temporary"], r["name"].lower()))
    return _rows_sorted(rows)


def _position_options(rows: list[dict]) -> list[dict]:
    """The position dropdown: each category present, with the specific roles
    found under it. Only what the data actually contains is offered."""
    seen: dict[str, set[str]] = {}
    for row in rows:
        category = row["position_category"]
        if category is None:
            continue
        roles = seen.setdefault(category, set())
        canonical = canonical_position(row["position"])
        if canonical is not None:
            roles.add(canonical.role)
    return [
        {"category": category, "roles": sorted(seen[category])}
        for category in sorted(seen, key=category_index)
    ]


async def list_players(
    *,
    q: str | None = None,
    team: str | None = None,
    decision: str | None = None,  # a decision label, or NO_DECISION
    rating_min: float | None = None,
    position: str | None = None,  # a category or a specific role
    age_bucket: str | None = None,
    foot: str | None = None,
    nationality: str | None = None,
    sort: str | None = None,
) -> dict:
    """All prospects, filtered in Python. `q` is accent-insensitive (matched
    against the same normalized columns the bot keys identities on). Returns the
    values actually present for each dropdown, so no filter offers a dead end."""
    prospects = await Prospect.all().prefetch_related("observations")
    all_rows = [_player_row(p) for p in prospects]

    rows = []
    for p, row in zip(prospects, all_rows):
        if q:
            qn = normalize_name(q)
            haystack = f"{p.normalized_name} {p.normalized_team or ''}"
            if qn not in haystack:
                continue
        if team and p.team != team:
            continue
        if decision and (row["decision"] or NO_DECISION) != decision:
            continue
        if rating_min is not None and (row["rating"] or 0) < rating_min:
            continue
        if not _matches_position(row, position):
            continue
        if not _in_age_bucket(row["age"], age_bucket):
            continue
        if foot and row["foot"] != foot:
            continue
        if nationality and row["nationality"] != nationality:
            continue
        rows.append(row)

    return {
        "players": _sorted_by(rows, sort),
        "teams": sorted({r["team"] for r in all_rows if r["team"]}),
        "decision_options": _ordered_labels(
            {r["decision"] or NO_DECISION for r in all_rows}
        ),
        "position_options": _position_options(all_rows),
        "age_options": [{"key": k, "label": label} for k, label, _, _ in AGE_BUCKETS],
        "foot_options": sorted({r["foot"] for r in all_rows if r["foot"]}),
        "nationality_options": sorted(
            {r["nationality"] for r in all_rows if r["nationality"]}
        ),
        "sort_options": [{"key": k, "label": label} for k, label in SORT_OPTIONS],
    }


async def decision_board(
    *,
    position: str | None = None,
    age_bucket: str | None = None,
    team: str | None = None,
) -> dict:
    """Prospects grouped by decision, best group first — the shortlist view.

    Within a decision, players are grouped by position category in pitch order:
    a shortlist is read position by position, never as one flat ranking."""
    prospects = await Prospect.all().prefetch_related("observations")
    all_rows = [_player_row(p) for p in prospects]
    await _add_trends(list(prospects), all_rows)

    kept = [
        row
        for row in all_rows
        if _matches_position(row, position)
        and _in_age_bucket(row["age"], age_bucket)
        and (not team or row["team"] == team)
    ]

    by_decision: dict[str, list[dict]] = {}
    for row in kept:
        by_decision.setdefault(row["decision"] or NO_DECISION, []).append(row)

    groups = []
    for label in _ordered_labels(set(by_decision)):
        members = by_decision[label]
        by_category: dict[str | None, list[dict]] = {}
        for row in members:
            by_category.setdefault(row["position_category"], []).append(row)
        groups.append(
            {
                "label": label,
                "count": len(members),
                "positions": [
                    {
                        "category": category or "Sin posición",
                        "players": _rows_sorted(by_category[category]),
                    }
                    for category in sorted(by_category, key=category_index)
                ],
            }
        )

    return {
        "groups": groups,
        "teams": sorted({r["team"] for r in all_rows if r["team"]}),
        "position_options": _position_options(all_rows),
        "age_options": [{"key": k, "label": label} for k, label, _, _ in AGE_BUCKETS],
    }


async def player_detail(prospect_id: int) -> dict | None:
    """One prospect: bio header, rating history, observations grouped by match
    (newest match first, notes in capture order within each)."""
    p = await Prospect.get_or_none(id=prospect_id)
    if p is None:
        return None
    await p.fetch_related("observations")
    obs = await (
        Observation.filter(prospect_id=prospect_id)
        .order_by("created_at", "id")
        .prefetch_related("session")
    )

    by_match: dict[int, dict] = {}
    history = []
    for o in obs:
        s = o.session
        group = by_match.setdefault(
            s.id,
            {
                "session_id": s.id,
                "home_team": s.home_team,
                "away_team": s.away_team,
                "competition": s.competition,
                "date": s.match_date or s.created_at,
                "is_active": s.state == SESSION_ACTIVE,
                "observations": [],
            },
        )
        group["observations"].append(
            {
                "minute": o.minute,
                "quote": o.raw_quote,
                "rating": o.rating,
                "is_substitution": o.is_substitution,
            }
        )
        if o.rating is not None:
            history.append(
                {
                    "rating": o.rating,
                    "date": s.match_date or s.created_at,
                    "match": f"{s.home_team} vs {s.away_team}",
                    "session_id": s.id,
                }
            )
    matches = sorted(by_match.values(), key=lambda g: g["date"], reverse=True)

    return {
        "player": {
            "id": p.id,
            "name": display_name(p),
            "is_temporary": p.is_temporary or not p.name,
            "team": p.team,
            "position": p.position,
            "position_abbr": position_abbr(p.position),
            "position_category": position_category(p.position),
            "age": current_age(p.birth_year, p.age),
            "birth_year": p.birth_year,
            "height_cm": p.height_cm,
            "weight_kg": p.weight_kg,
            "foot": p.preferred_foot,
            "shirt_number": p.shirt_number,
            "nationality": p.nationality,
            "origin_club": p.origin_club,
            "agent_name": p.agent_name,
            "agent_phone": p.agent_phone,
            "market_value_usd": p.market_value_usd,
            "contract_year": p.contract_year,
            "rating": p.latest_rating,
            "decision": prospect_decision(p),
            "notes": p.notes,
            "matches": len(matches),
            "observations": len(obs),
        },
        "matches": matches,
        "rating_history": history,
    }


async def identity_collision(
    prospect: Prospect, normalized_name: str, normalized_team: str
) -> Prospect | None:
    """A *different* prospect of the same scout already keyed to this identity.

    Renaming into an existing player must not create a second record for one
    person — the caller sends the client to the merge flow instead."""
    if not normalized_name:
        return None
    return await (
        Prospect.filter(
            agent_chat_id=prospect.agent_chat_id,
            normalized_name=normalized_name,
            normalized_team=normalized_team,
        )
        .exclude(id=prospect.id)
        .prefetch_related("observations")  # display_name reads them
        .first()
    )


async def get_prospect(prospect_id: int) -> Prospect | None:
    """A prospect ready for `display_name` (observations prefetched)."""
    return await (
        Prospect.filter(id=prospect_id).prefetch_related("observations").first()
    )


async def merge_candidates(prospect_id: int) -> dict | None:
    """The prospect plus every other one it could be merged with.

    Ordered by how likely they are the same person: fuzzy name match first, then
    same team, then the rest — the scout still confirms explicitly."""
    keep = await Prospect.get_or_none(id=prospect_id)
    if keep is None:
        return None
    await keep.fetch_related("observations")
    others = await (
        Prospect.filter(agent_chat_id=keep.agent_chat_id)
        .exclude(id=keep.id)
        .prefetch_related("observations")
    )

    def rank(p: Prospect) -> tuple:
        same_name = bool(keep.name and p.name and name_matches(keep.name, p.name))
        same_team = bool(keep.normalized_team) and p.normalized_team == keep.normalized_team
        return (not same_name, not same_team, display_name(p).lower())

    return {
        "keep": _player_row(keep),
        "candidates": [_player_row(p) for p in sorted(others, key=rank)],
    }


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
