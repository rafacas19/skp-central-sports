"""Score sheets: saving a player's per-match evaluation from the dashboard.

A sheet is stored as an `Evaluation` (the criterion scores) and mirrored onto
one ordinary rated `Observation` for the same match and player. That mirror is
what keeps the rest of the product unchanged: the rating history, the decision,
the match page, the Excel exports and the AI summary all read observations, so a
web score reaches them exactly like a rating the scout sent to the bot.

The player's headline rating (`latest_rating`, and the decision derived from it)
follows his most recent rated match by match date. Scoring an older match after
the fact adds it to the history without overwriting a newer rating.
"""

from __future__ import annotations

from datetime import datetime

from .models import (
    AWAY,
    HOME,
    SOURCE_WEB,
    Evaluation,
    Observation,
    Prospect,
    Session,
    decision_for_rating,
)
from .profiles import SCORE_MAX, SCORE_MIN, Profile, clean_scores, match_rating
from .taxonomy import normalize_name


class MergeConflict(Exception):
    """Two profiles of one player were both scored in the same match.

    Merging them would leave two sheets for one (match, player) pair — one of
    them would have to be thrown away. The scout decides which, not the merge."""


def match_when(session: Session) -> datetime:
    """When a match was played: the date the scout gave, else its creation."""
    return session.match_date or session.created_at


def _side(session: Session, team: str | None) -> str | None:
    if not team:
        return None
    norm = normalize_name(team)
    if norm == normalize_name(session.home_team):
        return HOME
    if norm == normalize_name(session.away_team):
        return AWAY
    return None


def carry_scores(old: Profile | None, new: Profile, scores: dict) -> dict[str, int]:
    """Scores that survive switching a sheet from one profile to another.

    Codes are not comparable across profiles ("2.1" is Posicionamiento
    Defensivo for a Lateral, Presión for an Extremo), so a score carries over
    only when the new profile has a criterion of the same name."""
    if old is None or not scores:
        return {}
    by_name = {normalize_name(c.name): c.code for c in new.criteria}
    carried = {}
    for c in old.criteria:
        if c.code in scores:
            code = by_name.get(normalize_name(c.name))
            if code is not None:
                carried[code] = scores[c.code]
    return clean_scores(new, carried)


def clean_single_rating(raw) -> float | None:
    """A goalkeeper's single match rating: a whole 1–5, anything else is none."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return float(value) if SCORE_MIN <= value <= SCORE_MAX else None


async def save_evaluation(
    session: Session,
    prospect: Prospect,
    *,
    profile: Profile | None,
    raw_scores: dict,
    build_ok: bool | None = None,
    height_ok: bool | None = None,
    note: str | None = None,
    single_rating: float | None = None,
) -> Evaluation:
    """Create or replace the player's sheet for this match, and its mirror.

    With a profile the rating is the mean of the valid scores (none scored ⇒ no
    rating); without one it is `single_rating`. Saving again updates the same
    sheet and the same observation — never a second of either."""
    if profile is not None:
        scores = clean_scores(profile, raw_scores)
        rating = match_rating(scores)
    else:
        scores = {}
        rating = single_rating
    note = (note or "").strip() or None

    evaluation = await Evaluation.get_or_none(session_id=session.id, prospect_id=prospect.id)
    if evaluation is None:
        evaluation = Evaluation(session_id=session.id, prospect_id=prospect.id)
    evaluation.profile = profile.key if profile is not None else None
    evaluation.scores = scores
    evaluation.build_ok = build_ok if profile is not None else None
    evaluation.height_ok = height_ok if profile is not None else None
    evaluation.rating = rating
    evaluation.note = note
    await evaluation.save()

    await _mirror(evaluation, session, prospect, profile)
    if rating is not None:
        await _refresh_headline(prospect, session, rating)
    return evaluation


async def _mirror(
    evaluation: Evaluation, session: Session, prospect: Prospect, profile: Profile | None
) -> None:
    """Keep the one observation that stands for this sheet in step with it."""
    obs = None
    if evaluation.observation_id is not None:
        obs = await Observation.get_or_none(id=evaluation.observation_id)
    if evaluation.rating is None and evaluation.note is None:
        # An empty sheet says nothing: no rating to chart, no note to read.
        if obs is not None:
            await obs.delete()
        return
    if profile is not None:
        quote = evaluation.note or f"Evaluación por perfil: {profile.name}"
    else:
        quote = evaluation.note or "Valoración del partido"
    fields = {
        "session_id": session.id,
        "prospect_id": prospect.id,
        "side": _side(session, prospect.team),
        "team": prospect.team,
        "player_name": prospect.name or None,
        "player_number": prospect.shirt_number,
        "player_position": prospect.position,
        "source": SOURCE_WEB,
        "rating": evaluation.rating,
        "raw_quote": quote,
    }
    if obs is None:
        obs = await Observation.create(**fields)
        evaluation.observation_id = obs.id
        await evaluation.save(update_fields=["observation_id"])
    else:
        await Observation.filter(id=obs.id).update(**fields)


async def _refresh_headline(prospect: Prospect, session: Session, rating: float) -> None:
    """Set the headline rating and decision — only if no later match is rated."""
    others = await (
        Observation.filter(prospect_id=prospect.id, rating__not_isnull=True)
        .exclude(session_id=session.id)
        .prefetch_related("session")
    )
    this_match = match_when(session)
    if any(match_when(o.session) > this_match for o in others):
        return
    decision = decision_for_rating(rating)
    await Prospect.filter(id=prospect.id).update(
        latest_rating=rating, decision_status=decision
    )
    prospect.latest_rating = rating
    prospect.decision_status = decision


async def check_merge(keep_id: int, drop_id: int) -> None:
    """Raise MergeConflict if both profiles have a sheet for the same match."""
    kept = set(
        await Evaluation.filter(prospect_id=keep_id).values_list("session_id", flat=True)
    )
    if not kept:
        return
    clash = await Evaluation.filter(prospect_id=drop_id, session_id__in=list(kept)).exists()
    if clash:
        raise MergeConflict(
            "Los dos perfiles tienen una evaluación del mismo partido. "
            "Borra una de las dos antes de fusionarlos."
        )


WEB_QUOTE_PREFIX = "Evaluación por perfil"


def is_placeholder_quote(text: str | None) -> bool:
    """The stand-in text of a score sheet with no note: nothing for a reader."""
    return bool(text) and (text.startswith(WEB_QUOTE_PREFIX) or text == "Valoración del partido")


async def summary_payload(prospect_id: int) -> list[dict]:
    """A player's score sheets as the AI summarizer reads them: per match, the
    profile, the rating and each section's average with its strongest and
    weakest criteria by name — the numbers turned into something to write from."""
    from .profiles import average, format_average, get_profile

    sheets = await Evaluation.filter(prospect_id=prospect_id).prefetch_related("session")
    out = []
    for e in sorted(sheets, key=lambda e: match_when(e.session)):
        s = e.session
        entry = {
            "date": match_when(s).date().isoformat(),
            "match": f"{s.home_team} vs {s.away_team}",
            "profile": None,
            "rating": e.rating,
            "note": e.note or "",
        }
        profile = get_profile(e.profile)
        if profile is not None:
            entry["profile"] = profile.name
            sections = []
            for section in profile.sections:
                scored = [(c.name, e.scores.get(c.code)) for c in section.criteria
                          if e.scores.get(c.code) is not None]
                if not scored:
                    continue
                sections.append({
                    "section": section.title,
                    "average": format_average(average([v for _, v in scored])),
                    "strengths": [n for n, v in scored if v >= 4],
                    "weaknesses": [n for n, v in scored if v <= 2],
                })
            entry["sections"] = sections
            entry["build_ok"] = e.build_ok
            entry["height_ok"] = e.height_ok
        out.append(entry)
    return out


async def sheets_marker(prospect_id: int) -> str:
    """Fingerprint of a player's score sheets: re-scoring changes it even when
    the number of observations stays the same."""
    rows = await Evaluation.filter(prospect_id=prospect_id).order_by("id").values_list("id", "updated_at")
    return "|".join(f"{i}:{u.isoformat()}" for i, u in rows)
