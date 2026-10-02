"""Matches run from the dashboard: create one, set both lineups, see the pitch.

A web match is an ordinary `Session` with `origin="web"`, so it appears in the
match list and every player page like a bot match — but the bot never treats it
as the match a Telegram note belongs to (see `Storage.get_active_session`).

Lineup players are found or created exactly like the bot does it — by name and
club via `Storage.get_or_create_prospect` — so typing a known player reuses his
profile instead of creating a second one. A slot with only a shirt number gets
a temporary profile for that match, as a number-only note does in the bot.

Any match, including one captured by the bot, can be given a lineup: the pitch
is just a faster way to reach each player's score sheet.
"""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ..categories import split_category
from ..evaluations import MergeConflict, match_when
from ..formations import BENCH_ROWS, FORMATIONS, get_formation, pitch_point
from ..models import (
    AWAY,
    HOME,
    ORIGIN_WEB,
    SESSION_ACTIVE,
    SOURCE_WEB,
    Evaluation,
    MatchPlayer,
    Observation,
    Prospect,
    Session,
    decision_for_rating,
)
from ..positions import position_abbr
from ..storage import Storage
from ..taxonomy import normalize_name
from . import auth, queries
from .router import _render

router = APIRouter(prefix="/dashboard", include_in_schema=False)

SIDES = (HOME, AWAY)
_MAX_TEAM = 120
_MAX_TEXT = 200


def _team(session: Session, side: str) -> str:
    return session.home_team if side == HOME else session.away_team


def _missing(request: Request):
    return _render(
        request, "not_found.html",
        {"message": "Ese partido no existe.", "back": "/dashboard/partidos"},
        status_code=status.HTTP_404_NOT_FOUND,
    )


_PARTICLES = {"de", "del", "la", "las", "los", "y", "da", "do", "dos", "van", "von"}


def short_name(name: str | None) -> str:
    """"Camilo Restrepo Vélez" → "C. Restrepo" — what fits under a shirt.

    Spanish names put the first surname after the given names: with four
    words ("Jhojan Felipe Zúñiga Gómez") that is the third, otherwise the
    second. Particles stay with their surname ("J. De La Rosa")."""
    parts = (name or "").split()
    if len(parts) < 2:
        return parts[0] if parts else ""
    core = [i for i, w in enumerate(parts) if w.lower() not in _PARTICLES]
    if len(core) < 2:
        return " ".join(parts)
    pick = core[2] if len(core) >= 4 else core[1]
    start = pick
    while start > 0 and parts[start - 1].lower() in _PARTICLES:
        start -= 1
    return f"{parts[core[0]][0]}. {' '.join(parts[start:pick + 1])}"


def _number(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() and int(raw) < 1000 else None


# ── New match ────────────────────────────────────────────────────────────
_NEW_FIELDS = ("local", "visitante", "fecha", "competicion", "categoria", "sede")


def _new_values(values: dict | None = None) -> dict:
    values = values or {}
    out = {f: (values.get(f) or "").strip() for f in _NEW_FIELDS}
    if not out["fecha"]:
        out["fecha"] = datetime.now(queries.TZ).date().isoformat()
    return out


@router.get(
    "/partidos/nuevo", response_class=HTMLResponse, dependencies=[Depends(auth.require_dashboard)]
)
async def match_new_page(request: Request):
    return _render(request, "match_new.html", {"values": _new_values(), "errors": {}})


def _validate_match(values: dict, *, teams: bool = True) -> tuple[dict[str, str], date | None]:
    """Field errors for the match form, and the parsed date."""
    errors: dict[str, str] = {}
    if teams:
        for field, label in (("local", "El equipo local"), ("visitante", "El equipo visitante")):
            if not values[field]:
                errors[field] = f"{label} es obligatorio."
            elif len(values[field]) > _MAX_TEAM:
                errors[field] = f"{label} no puede pasar de {_MAX_TEAM} caracteres."
        if not errors and normalize_name(values["local"]) == normalize_name(values["visitante"]):
            errors["visitante"] = "Los dos equipos no pueden ser el mismo."
    for field in ("competicion", "categoria", "sede"):
        if len(values[field]) > _MAX_TEXT:
            errors[field] = f"No puede pasar de {_MAX_TEXT} caracteres."
    played = None
    try:
        played = date.fromisoformat(values["fecha"])
    except ValueError:
        errors["fecha"] = "Fecha no válida."
    return errors, played


def _noon(played: date) -> datetime:
    # Midday in the scout's timezone: a bare date must not slip a day in UTC.
    return datetime.combine(played, time(12, 0), tzinfo=queries.TZ)


@router.post("/partidos/nuevo", dependencies=[Depends(auth.require_dashboard)])
async def match_new_submit(request: Request):
    submitted = {k: str(v) for k, v in (await request.form()).items()}
    auth.require_csrf(submitted.get(auth.CSRF_FIELD))
    values = _new_values(submitted)
    errors, played = _validate_match(values)
    if errors:
        return _render(
            request, "match_new.html", {"values": values, "errors": errors},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    storage = Storage()
    chat_id = await queries.scout_chat_id()
    session = await storage.create_session(
        chat_id,
        values["local"],
        values["visitante"],
        None,
        origin=ORIGIN_WEB,
        competition=values["competicion"] or None,
        category=values["categoria"] or None,
        location=values["sede"] or None,
        # Who created it on the panel; the bot's scout name as a fallback.
        scout_name=getattr(await auth.current_user(request), "display_name", None)
        or await storage.get_scout_name(chat_id),
        match_date=_noon(played),
    )
    return RedirectResponse(
        f"/dashboard/partidos/{session.id}/sistema?nuevo=1", status_code=status.HTTP_303_SEE_OTHER
    )


# ── Editing and deleting a match ────────────────────────────────────────
def _edit_values(session: Session) -> dict:
    when = (session.match_date or session.created_at).astimezone(queries.TZ)
    return {
        "local": session.home_team, "visitante": session.away_team,
        "fecha": when.date().isoformat(), "competicion": session.competition or "",
        "categoria": session.category or "", "sede": session.location or "",
    }


def _edit_context(session: Session, values: dict, errors: dict) -> dict:
    return {
        "values": values, "errors": errors,
        "edit": {"id": session.id, "teams": session.origin == ORIGIN_WEB,
                 "title": f"{session.home_team} vs {session.away_team}"},
    }


def _temp_key_prefix(session_id: int) -> str:
    """Temporary players created for one match share this synthetic key prefix."""
    return f"__temp__:{session_id}:"


@router.get(
    "/partidos/{session_id}/editar",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def match_edit_page(request: Request, session_id: int):
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    return _render(request, "match_new.html", _edit_context(session, _edit_values(session), {}))


@router.post("/partidos/{session_id}/editar", dependencies=[Depends(auth.require_dashboard)])
async def match_edit_submit(request: Request, session_id: int):
    """Correct a match's details. The teams can be renamed only on a web match:
    a bot match's team names are what the bot matched the scout's notes on.

    Renaming a team relabels the match's own unidentified players (they belong
    to this match), never a named player — his club is his identity."""
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    submitted = {k: str(v) for k, v in (await request.form()).items()}
    auth.require_csrf(submitted.get(auth.CSRF_FIELD))
    teams = session.origin == ORIGIN_WEB
    values = {**_edit_values(session), **_new_values(submitted)}
    if not teams:
        values["local"], values["visitante"] = session.home_team, session.away_team
    errors, played = _validate_match(values, teams=teams)
    if errors:
        return _render(request, "match_new.html", _edit_context(session, values, errors),
                       status_code=status.HTTP_400_BAD_REQUEST)

    updates = {
        "competition": values["competicion"] or None,
        "category": values["categoria"] or None,
        "location": values["sede"] or None,
        "match_date": _noon(played),
    }
    if teams:
        for side, field in ((HOME, "local"), (AWAY, "visitante")):
            old = _team(session, side)
            club, category = split_category(values[field])
            updates[f"{side}_team"] = club
            updates[f"{side}_team_category"] = category
            if normalize_name(club) != normalize_name(old):
                await Prospect.filter(
                    normalized_name__startswith=_temp_key_prefix(session_id), team=old
                ).update(team=club, normalized_team=normalize_name(club))
                await Observation.filter(session_id=session_id, team=old).update(team=club)
    await Session.filter(id=session_id).update(**updates)
    return RedirectResponse(f"/dashboard/partidos/{session_id}", status_code=status.HTTP_303_SEE_OTHER)


async def delete_match(session: Session) -> None:
    """Remove a web match and everything that only existed because of it.

    Its observations, score sheets and lineup go with it (FK cascades); its
    unidentified players go too once nothing else refers to them. A named
    player whose headline rating came from this match falls back to his most
    recent remaining rated match — or to no rating when there is none."""
    sid = session.id
    touched = set(await Evaluation.filter(session_id=sid).values_list("prospect_id", flat=True))
    rated_here = {
        o.prospect_id: o.rating
        for o in await Observation.filter(session_id=sid, rating__not_isnull=True, prospect_id__not_isnull=True)
    }
    touched |= set(rated_here)
    await Session.filter(id=sid).delete()

    for p in await Prospect.filter(normalized_name__startswith=_temp_key_prefix(sid)):
        if not (await Observation.filter(prospect_id=p.id).exists()
                or await Evaluation.filter(prospect_id=p.id).exists()
                or await MatchPlayer.filter(prospect_id=p.id).exists()):
            await p.delete()

    for pid in touched:
        p = await Prospect.get_or_none(id=pid)
        if p is None or p.latest_rating is None or pid not in rated_here:
            continue
        if p.latest_rating != rated_here[pid]:
            continue  # his headline came from somewhere else
        remaining = await (
            Observation.filter(prospect_id=pid, rating__not_isnull=True).prefetch_related("session")
        )
        if remaining:
            latest = max(remaining, key=lambda o: (match_when(o.session), o.created_at))
            rating = latest.rating
            await Prospect.filter(id=pid).update(
                latest_rating=rating, decision_status=decision_for_rating(rating)
            )
        else:
            auto = p.decision_status == decision_for_rating(p.latest_rating)
            await Prospect.filter(id=pid).update(
                latest_rating=None, **({"decision_status": None} if auto else {})
            )


@router.get(
    "/partidos/{session_id}/borrar",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def match_delete_page(request: Request, session_id: int):
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    return _render(request, "match_delete.html", {
        "match": {"id": session.id, "title": f"{session.home_team} vs {session.away_team}",
                  "date": session.match_date or session.created_at,
                  "is_web": session.origin == ORIGIN_WEB},
        "counts": {
            "evaluations": await Evaluation.filter(session_id=session_id).count(),
            "observations": await Observation.filter(session_id=session_id).count(),
            "lineup": await MatchPlayer.filter(session_id=session_id).count(),
        },
    })


@router.post("/partidos/{session_id}/borrar", dependencies=[Depends(auth.require_dashboard)])
async def match_delete_submit(request: Request, session_id: int, csrf: str = Form(default="")):
    """Delete a match created on the panel. Bot matches are not deletable here:
    they are the scout's field record, captured note by note."""
    auth.require_csrf(csrf)
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    if session.origin != ORIGIN_WEB:
        return RedirectResponse(f"/dashboard/partidos/{session_id}", status_code=status.HTTP_303_SEE_OTHER)
    await delete_match(session)
    return RedirectResponse("/dashboard/partidos?aviso=borrado", status_code=status.HTTP_303_SEE_OTHER)


# ── Formation step ───────────────────────────────────────────────────────
def formation_cards(selected: str | None) -> list[dict]:
    """Every preset with a miniature of where its eleven stand (home view)."""
    chosen, _ = get_formation(selected)
    return [
        {
            "name": name,
            "selected": name == chosen,
            "dots": [pitch_point(slot, away=False) for slot in slots],
        }
        for name, slots in FORMATIONS.items()
    ]


@router.get(
    "/partidos/{session_id}/sistema",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def formation_page(request: Request, session_id: int, nuevo: str | None = None):
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    sides = [
        {"side": side, "team": _team(session, side),
         "cards": formation_cards(getattr(session, f"{side}_formation"))}
        for side in SIDES
    ]
    return _render(
        request, "formation.html",
        {
            "match": {"id": session.id, "home_team": session.home_team, "away_team": session.away_team},
            "sides": sides,
            "new": bool(nuevo),
            "has_lineup": await MatchPlayer.filter(session_id=session_id).exists(),
        },
    )


@router.post("/partidos/{session_id}/sistema", dependencies=[Depends(auth.require_dashboard)])
async def formation_submit(request: Request, session_id: int):
    """Store both formations. In the new-match flow the optional lineup step
    comes next; changed later, it goes back to the pitch."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    auth.require_csrf(form.get(auth.CSRF_FIELD))
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    await Session.filter(id=session_id).update(
        **{f"{side}_formation": get_formation(form.get(f"{side}-f"))[0] for side in SIDES}
    )
    target = (f"/dashboard/partidos/{session_id}/alineacion?nuevo=1" if form.get("nuevo")
              else f"/dashboard/partidos/{session_id}/campo")
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


# ── Lineup editor ────────────────────────────────────────────────────────
async def _known_players(team: str) -> list[str]:
    """Named players already on file for this club — the name suggestions."""
    rows = await Prospect.filter(
        normalized_team=normalize_name(team), is_temporary=False
    ).exclude(name="")
    return sorted({p.name for p in rows}, key=normalize_name)


async def lineup_context(session: Session) -> dict:
    rows = await MatchPlayer.filter(session_id=session.id).prefetch_related("prospect")
    sides = []
    for side in SIDES:
        formation_name, slots = get_formation(getattr(session, f"{side}_formation"))
        mine = [r for r in rows if r.side == side]
        by_slot = {r.slot: r for r in mine if r.slot is not None}
        starters = []
        for i, slot in enumerate(slots):
            r = by_slot.get(i)
            starters.append(
                {
                    "index": i,
                    "role": slot.role,
                    "number": r.shirt_number if r and r.shirt_number is not None else "",
                    "name": r.prospect.name if r else "",
                }
            )
        bench_rows = [r for r in mine if r.slot is None]
        bench = [
            {"number": r.shirt_number if r.shirt_number is not None else "", "name": r.prospect.name}
            for r in bench_rows
        ]
        bench += [{"number": "", "name": ""}] * max(3, BENCH_ROWS - len(bench))
        sides.append(
            {
                "side": side,
                "team": _team(session, side),
                "formation": formation_name,
                "starters": starters,
                "bench": list(enumerate(bench)),
                "known": await _known_players(_team(session, side)),
            }
        )
    return {
        "match": {"id": session.id, "home_team": session.home_team, "away_team": session.away_team},
        "sides": sides,
    }


async def _resolve(
    storage: Storage, session: Session, team: str, name: str, number: int | None, role: str | None
) -> Prospect:
    if name:
        prospect = await storage.get_or_create_prospect(
            session.agent_chat_id, name, team, position=role
        )
    else:
        prospect = await storage.get_or_create_temp_prospect(
            session.agent_chat_id, session.id, team, number
        )
        if role and not prospect.position:
            prospect.position = role
    if number is not None and prospect.shirt_number is None:
        prospect.shirt_number = number
    await prospect.save()
    return prospect


def is_unnamed(prospect: Prospect) -> bool:
    """A player known only by a shirt number (a temporary, match-scoped profile)."""
    return prospect.is_temporary or not prospect.name


async def name_player(prospect: Prospect, name: str) -> Prospect:
    """Give an unnamed player his name, keeping every score and lineup place.

    If a player with that name already exists at the club, the two are the same
    person: the unnamed profile is merged into the existing one, which is
    returned. Callers that must ask first check `queries.identity_taken`."""
    from ..taxonomy import normalize_identity

    name = name.strip()[:_MAX_TEXT]
    norm = normalize_identity(name)
    existing = await queries.identity_taken(
        prospect.agent_chat_id, norm, prospect.normalized_team or "", exclude_id=prospect.id
    )
    if existing is not None:
        await Storage().merge_prospects(existing.id, prospect.id)
        return await Prospect.get(id=existing.id)
    prospect.name, prospect.normalized_name, prospect.is_temporary = name, norm, False
    await prospect.save()
    return prospect


async def save_lineup(session: Session, form: dict[str, str]) -> None:
    """Replace both lineups with what the form holds. Every field is optional.

    A row with neither a name nor a number is an empty position — unless an
    unidentified player already stands there (one the scout tapped on the
    pitch): he keeps his place, and his score with it. Typing a number or a name
    over such a player identifies *him* rather than creating somebody new. A
    player entered twice (two rows, or both sides) keeps his first place only."""
    storage = Storage()
    before = await MatchPlayer.filter(session_id=session.id).prefetch_related("prospect")
    by_slot = {(r.side, r.slot): r.prospect for r in before if r.slot is not None}
    by_number = {(r.side, r.shirt_number): r.prospect for r in before if r.shirt_number is not None}
    # Substitution history survives a lineup edit: it belongs to the player.
    sub_flags = {
        r.prospect_id: {"came_on_for_id": r.came_on_for_id, "subbed_off": r.subbed_off,
                        "sub_minute": r.sub_minute}
        for r in before
    }

    entries: list[tuple[str, int | None, str, int | None, str | None]] = []
    formations = {}
    for side in SIDES:
        # The formation is chosen on its own step; a posted one still wins.
        formation_name, slots = get_formation(
            form.get(f"{side}-f") or getattr(session, f"{side}_formation")
        )
        formations[f"{side}_formation"] = formation_name
        for i, slot in enumerate(slots):
            name = (form.get(f"{side}-s{i}-p") or "").strip()[:_MAX_TEXT]
            number = _number(form.get(f"{side}-s{i}-n"))
            held = by_slot.get((side, i))
            if name or number is not None or (held is not None and is_unnamed(held)):
                entries.append((side, i, name, number, slot.role))
        j = 0
        while f"{side}-b{j}-p" in form or f"{side}-b{j}-n" in form:
            name = (form.get(f"{side}-b{j}-p") or "").strip()[:_MAX_TEXT]
            number = _number(form.get(f"{side}-b{j}-n"))
            if name or number is not None:
                entries.append((side, None, name, number, None))
            j += 1

    await Session.filter(id=session.id).update(**formations)
    await MatchPlayer.filter(session_id=session.id).delete()
    placed: set[int] = set()
    for side, slot, name, number, role in entries:
        held = by_slot.get((side, slot)) if slot is not None else None
        if held is None or not is_unnamed(held):
            held = by_number.get((side, number)) if number is not None else None
        if held is not None and is_unnamed(held):
            prospect = held
            if name:
                try:
                    prospect = await name_player(held, name)
                except MergeConflict:
                    prospect = await _resolve(storage, session, _team(session, side), name, number, role)
            if number is not None and (prospect.shirt_number is None or is_unnamed(prospect)):
                prospect.shirt_number = number
                await prospect.save()
        else:
            prospect = await _resolve(storage, session, _team(session, side), name, number, role)
        if prospect.id in placed:
            continue
        placed.add(prospect.id)
        await MatchPlayer.create(
            session_id=session.id, prospect_id=prospect.id, side=side,
            slot=slot, shirt_number=number, **sub_flags.get(prospect.id, {}),
        )


@router.get(
    "/partidos/{session_id}/alineacion",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def lineup_page(request: Request, session_id: int, nuevo: str | None = None):
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    return _render(request, "lineup.html", {**await lineup_context(session), "new": bool(nuevo)})


@router.post("/partidos/{session_id}/alineacion", dependencies=[Depends(auth.require_dashboard)])
async def lineup_submit(request: Request, session_id: int):
    form = {k: str(v) for k, v in (await request.form()).items()}
    auth.require_csrf(form.get(auth.CSRF_FIELD))
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    await save_lineup(session, form)
    return RedirectResponse(
        f"/dashboard/partidos/{session_id}/campo", status_code=status.HTTP_303_SEE_OTHER
    )


# ── Pitch ────────────────────────────────────────────────────────────────
async def pitch_context(session: Session) -> dict:
    rows = await MatchPlayer.filter(session_id=session.id).prefetch_related("prospect")
    ratings = {
        e.prospect_id: e.rating
        for e in await Evaluation.filter(session_id=session.id)
    }
    tokens, benches = [], {HOME: [], AWAY: []}
    # Who replaced whom, so a substituted player can offer "Deshacer cambio".
    came_on = {r.came_on_for_id: r for r in rows if r.came_on_for_id and r.slot is not None}
    for side in SIDES:
        _, slots = get_formation(getattr(session, f"{side}_formation"))
        filled = {}
        for r in (r for r in rows if r.side == side):
            unnamed = is_unnamed(r.prospect)
            player = {
                "id": r.prospect_id,
                "came_on": bool(r.came_on_for_id) and r.slot is not None,
                "subbed_off": r.subbed_off,
                "sub_minute": r.sub_minute,
                "undo_row": came_on[r.prospect_id].id if r.subbed_off and r.prospect_id in came_on else None,
                "number": r.shirt_number if r.shirt_number is not None else "",
                "label": "" if unnamed else short_name(r.prospect.name),
                "name": (r.prospect.name if not unnamed
                         else f"Dorsal {r.shirt_number}" if r.shirt_number is not None
                         else "Sin identificar"),
                "unnamed": unnamed,
                "side": side,
                "evaluated": r.prospect_id in ratings,
                "rating": ratings.get(r.prospect_id),
            }
            if r.slot is not None and r.slot < len(slots):
                filled[r.slot] = player
            else:
                benches[side].append(player)
        # Every position of the formation is on the pitch, filled or not: an
        # empty one is still a player the scout can tap and score.
        for i, slot in enumerate(slots):
            left, top = pitch_point(slot, away=side == AWAY)
            abbr = position_abbr(slot.role) or ""
            player = filled.get(i) or {"empty": True, "slot": i, "side": side, "number": "",
                                       "name": slot.role, "unnamed": True, "evaluated": False,
                                       "rating": None, "label": ""}
            if not player["label"]:
                player = {**player, "label": abbr}
            tokens.append({**player, "left": left, "top": top, "role": slot.role})
    for side in SIDES:
        benches[side].sort(key=lambda p: (p["subbed_off"], p["number"] == "", p["number"] or 0))
    when = session.match_date or session.created_at
    actions = [
        {"href": f"/dashboard/partidos/{session.id}/cambio", "label": "Hacer un cambio", "write": True, "icon": "pitch"},
        {"href": f"/dashboard/partidos/{session.id}/alineacion", "label": "Dorsales y nombres", "write": True, "icon": "edit"},
        {"href": f"/dashboard/partidos/{session.id}/sistema", "label": "Cambiar sistema", "write": True, "icon": "pitch"},
        {"href": f"/dashboard/partidos/{session.id}", "label": "Detalle del partido", "icon": "list"},
        {"href": f"/dashboard/partidos/{session.id}/evaluaciones.xlsx", "label": "Excel de evaluaciones", "icon": "file"},
        {"href": f"/dashboard/partidos/{session.id}/editar", "label": "Editar partido", "write": True, "icon": "edit"},
        {"href": "/dashboard/partidos", "label": "Todos los partidos", "icon": "flag", "sep": True},
        {"href": "/dashboard/jugadores", "label": "Jugadores", "icon": "user"},
    ]
    if session.origin == ORIGIN_WEB and session.state == SESSION_ACTIVE:
        actions.append({"post": f"/dashboard/partidos/{session.id}/finalizar",
                        "label": "Finalizar partido", "write": True, "icon": "flag", "danger": True})
    unnamed = sum(1 for r in rows if is_unnamed(r.prospect))
    return {
        "bar": {
            "back": f"/dashboard/partidos/{session.id}",
            "back_label": "Volver al partido",
            "title": f"{session.home_team} vs {session.away_team}",
            "meta": f"{len(ratings)} puntuados" if ratings else "Toca un jugador para puntuarlo",
            "actions": actions,
        },
        "when": when,
        "unnamed": unnamed,
        "match": {
            "id": session.id,
            "home_team": session.home_team,
            "away_team": session.away_team,
            "date": session.match_date or session.created_at,
            "is_active": session.state == SESSION_ACTIVE,
            "is_web": session.origin == ORIGIN_WEB,
        },
        "tokens": tokens,
        "benches": [
            {"side": HOME, "team": session.home_team, "players": benches[HOME]},
            {"side": AWAY, "team": session.away_team, "players": benches[AWAY]},
        ],
        "has_lineup": True,
    }


@router.get(
    "/partidos/{session_id}/campo",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def pitch_page(request: Request, session_id: int):
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    return _render(request, "pitch.html", await pitch_context(session))


@router.post(
    "/partidos/{session_id}/puesto/{side}/{slot}", dependencies=[Depends(auth.require_dashboard)]
)
async def open_position(
    request: Request, session_id: int, side: str, slot: int, csrf: str = Form(default="")
):
    """Score an empty position: create the unidentified player who plays it
    and open his sheet. A POST — looking at the pitch creates nobody — and
    idempotent: tapping the same position again reopens the same player."""
    auth.require_csrf(csrf)
    session = await Session.get_or_none(id=session_id)
    if session is None or side not in SIDES:
        return _missing(request)
    _, slots = get_formation(getattr(session, f"{side}_formation"))
    if not 0 <= slot < len(slots):
        return _missing(request)
    row = await MatchPlayer.get_or_none(session_id=session_id, side=side, slot=slot)
    if row is None:
        team = _team(session, side)
        synthetic = f"__temp__:{session_id}:{normalize_name(team)}:puesto-{side}-{slot}"
        prospect = await Prospect.filter(
            agent_chat_id=session.agent_chat_id, normalized_name=synthetic
        ).first()
        if prospect is None:
            prospect = await Prospect.create(
                agent_chat_id=session.agent_chat_id, name="", normalized_name=synthetic,
                team=team, normalized_team=normalize_name(team),
                position=slots[slot].role, is_temporary=True,
            )
        row = await MatchPlayer.create(
            session_id=session_id, prospect_id=prospect.id, side=side, slot=slot
        )
    return RedirectResponse(
        f"/dashboard/partidos/{session_id}/jugadores/{row.prospect_id}/evaluar",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Substitutions ────────────────────────────────────────────────────────
def _player_label(row: MatchPlayer) -> str:
    name = row.prospect.name if not is_unnamed(row.prospect) else ""
    number = row.shirt_number if row.shirt_number is not None else row.prospect.shirt_number
    if name and number is not None:
        return f"{number} · {name}"
    return name or (f"Dorsal {number}" if number is not None else "Sin identificar")


async def _sub_context(session: Session, errors: dict | None = None, values: dict | None = None) -> dict:
    rows = await MatchPlayer.filter(session_id=session.id).prefetch_related("prospect")
    _, home_slots = get_formation(session.home_formation)
    _, away_slots = get_formation(session.away_formation)
    slots = {HOME: home_slots, AWAY: away_slots}
    sides = []
    for side in SIDES:
        on = [r for r in rows if r.side == side and r.slot is not None]
        on.sort(key=lambda r: r.slot)
        bench = [r for r in rows if r.side == side and r.slot is None and not r.subbed_off]
        sides.append({
            "side": side, "team": _team(session, side),
            "on": [{"id": r.id, "label": f"{_player_label(r)} ({slots[side][r.slot].role})"
                    if r.slot < len(slots[side]) else _player_label(r)} for r in on],
            "bench": [{"id": r.id, "label": _player_label(r)} for r in bench],
        })
    return {
        "match": {"id": session.id, "home_team": session.home_team, "away_team": session.away_team},
        "sides": sides, "errors": errors or {}, "values": values or {},
    }


@router.get(
    "/partidos/{session_id}/cambio",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_dashboard)],
)
async def substitution_page(request: Request, session_id: int, lado: str | None = None):
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    context = await _sub_context(session, values={"lado": lado if lado in SIDES else HOME})
    return _render(request, "substitution.html", context)


@router.post("/partidos/{session_id}/cambio", dependencies=[Depends(auth.require_dashboard)])
async def substitution_submit(request: Request, session_id: int):
    """Make a change: who comes on (a substitute on the bench, or someone typed
    now) for whom, and optionally at what minute.

    The player coming on takes the slot and the one going off moves to the bench
    marked as substituted — both keep their score sheets. An ordinary
    substitution observation is written too, as the bot does for "entra X sale Y"."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    auth.require_csrf(form.get(auth.CSRF_FIELD))
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    side = form.get("lado") if form.get("lado") in SIDES else HOME
    errors: dict[str, str] = {}
    out_row = None
    if (form.get(f"sale-{side}") or "").isdigit():
        out_row = await MatchPlayer.get_or_none(
            id=int(form[f"sale-{side}"]), session_id=session_id, side=side, slot__not_isnull=True
        ).prefetch_related("prospect")
    if out_row is None:
        errors["sale"] = "Elige quién sale."
    raw_minute = (form.get("minuto") or "").strip()
    minute = int(raw_minute) if raw_minute.isdigit() and int(raw_minute) <= 150 else None
    if raw_minute and minute is None:
        errors["minuto"] = "El minuto debe ser un número entre 0 y 150."

    in_row = None
    entra = form.get(f"entra-{side}") or ""
    if entra.isdigit():
        in_row = await MatchPlayer.get_or_none(
            id=int(entra), session_id=session_id, side=side, slot=None, subbed_off=False
        ).prefetch_related("prospect")
        if in_row is None:
            errors["entra"] = "Ese suplente ya no está en el banquillo."
    else:
        name = (form.get("nombre") or "").strip()[:_MAX_TEXT]
        number = _number(form.get("dorsal"))
        if not name and number is None:
            errors["entra"] = "Elige un suplente o escribe el nombre o el dorsal de quien entra."
        elif not errors:
            prospect = await _resolve(Storage(), session, _team(session, side), name, number, None)
            in_row = await MatchPlayer.get_or_none(session_id=session_id, prospect_id=prospect.id)
            if in_row is not None and (in_row.slot is not None or in_row.subbed_off):
                errors["entra"] = "Ese jugador ya ha jugado en este partido."
                in_row = None
            elif in_row is None:
                in_row = await MatchPlayer.create(session_id=session_id, prospect_id=prospect.id,
                                                  side=side, slot=None, shirt_number=number)
            await in_row.fetch_related("prospect")
    if errors:
        context = await _sub_context(session, errors, {**form, "lado": side})
        return _render(request, "substitution.html", context, status_code=status.HTTP_400_BAD_REQUEST)

    slot = out_row.slot
    await MatchPlayer.filter(id=out_row.id).update(slot=None, subbed_off=True, sub_minute=minute)
    await MatchPlayer.filter(id=in_row.id).update(
        slot=slot, came_on_for_id=out_row.prospect_id, sub_minute=minute
    )
    team = _team(session, side)
    await Observation.create(
        session_id=session_id, prospect_id=in_row.prospect_id, side=side, team=team,
        player_name=in_row.prospect.name or None, player_number=in_row.shirt_number,
        source=SOURCE_WEB, minute=minute, is_substitution=True,
        raw_quote=f"Entra {_player_label(in_row)} por {_player_label(out_row)}",
    )
    return RedirectResponse(f"/dashboard/partidos/{session_id}/campo", status_code=status.HTTP_303_SEE_OTHER)


@router.post(
    "/partidos/{session_id}/cambio/{row_id}/deshacer", dependencies=[Depends(auth.require_dashboard)]
)
async def substitution_undo(request: Request, session_id: int, row_id: int, csrf: str = Form(default="")):
    """Undo a change: the player who went off returns to his slot and the one
    who came on goes back to the bench. Only possible while that slot is still
    his (a later change on the same slot is undone first)."""
    auth.require_csrf(csrf)
    in_row = await MatchPlayer.get_or_none(id=row_id, session_id=session_id, came_on_for_id__not_isnull=True)
    if in_row is not None and in_row.slot is not None:
        out_row = await MatchPlayer.get_or_none(
            session_id=session_id, prospect_id=in_row.came_on_for_id, subbed_off=True
        )
        if out_row is not None:
            await MatchPlayer.filter(id=out_row.id).update(slot=in_row.slot, subbed_off=False, sub_minute=None)
            await MatchPlayer.filter(id=in_row.id).update(slot=None, came_on_for_id=None, sub_minute=None)
            await Observation.filter(
                session_id=session_id, prospect_id=in_row.prospect_id, is_substitution=True, source=SOURCE_WEB
            ).delete()
    return RedirectResponse(f"/dashboard/partidos/{session_id}/campo", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/partidos/{session_id}/finalizar", dependencies=[Depends(auth.require_dashboard)])
async def match_finish(request: Request, session_id: int, csrf: str = Form(default="")):
    """Close a web match. Bot matches are closed from Telegram (/finalizar),
    which also sends the report and asks about duplicates — not skipped here."""
    auth.require_csrf(csrf)
    session = await Session.get_or_none(id=session_id)
    if session is None:
        return _missing(request)
    if session.origin == ORIGIN_WEB and session.state == SESSION_ACTIVE:
        await Storage().end_session(session_id)
    return RedirectResponse(
        f"/dashboard/partidos/{session_id}", status_code=status.HTTP_303_SEE_OTHER
    )
