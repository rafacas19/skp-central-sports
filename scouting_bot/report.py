"""Report generation.

Pure functions over hydrated ORM instances (no Telegram/DB), so they're trivially
unit-testable:
  - build_summary(session): short Telegram summary of a finished match.
  - build_csv(session): the match report — one CSV row per observation.
  - build_markdown(session): full markdown match report (used by the REST API).
  - build_player_report(prospect, observations, summary): the accumulated,
    cross-match player report (raw history + AI summary + bio/decision header).

Observation-first: observations carry their own identity snapshot (team, player
name/number/position) and link to a cross-match Prospect. No sentiment/skill —
evaluation is the manual rating only.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict

from .models import Observation, Prospect, Session


def _opponent(session: Session, team: str | None) -> str:
    """The other team in 'A vs B' relative to `team` (blank if unknown)."""
    if not team:
        return ""
    from .taxonomy import normalize_name

    nt = normalize_name(team)
    if nt == normalize_name(session.home_team):
        return session.away_team
    if nt == normalize_name(session.away_team):
        return session.home_team
    return ""


def _match_label(session: Session) -> str:
    return f"{session.home_team} vs {session.away_team}"


def _fmt_date(value) -> str:
    if value is None:
        return ""
    try:
        return value.strftime("%Y-%m-%d")
    except AttributeError:
        return str(value)


# ── Telegram summary ──────────────────────────────────────────────────────────
def build_summary(session: Session) -> str:
    """Short summary shown directly in the Telegram chat after /finalizar."""
    player_obs = [o for o in session.observations if not o.is_team_note]
    team_notes = [o for o in session.observations if o.is_team_note]

    lines = [f"📋 *Informe del partido*: {_match_label(session)}"]
    if session.label:
        lines.append(f"_{session.label}_")
    lines.append(f"Observaciones registradas: *{len(session.observations)}*")

    # Most-noted players, grouped by their identity snapshot (name+team).
    by_player: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in player_obs:
        key = (o.player_name or f"#{o.player_number}" or "?", o.team or "")
        by_player[key].append(o)
    if by_player:
        lines.append("")
        lines.append("*Jugadores más observados*")
        ranked = sorted(by_player.items(), key=lambda kv: len(kv[1]), reverse=True)
        for (name, team), obs in ranked[:5]:
            team_str = f" ({team})" if team else ""
            lines.append(f"• {name}{team_str} — {len(obs)} obs.")

    if team_notes:
        lines.append("")
        lines.append(f"📝 Notas de equipo: {len(team_notes)}")

    lines.append("")
    lines.append("Informe completo adjunto como archivo. 📎")
    return "\n".join(lines)


# ── Match CSV (one row per observation) ─────────────────────────────────────────
_CSV_COLUMNS = [
    "Match",
    "Date",
    "Team",
    "Opponent",
    "Player name",
    "Number",
    "Position",
    "Age",
    "Height",
    "Observation",
    "Source",
    "Manual rating",
    "Scout",
    "Created at",
]


def build_csv(session: Session) -> bytes:
    """Match report as CSV bytes — one row per observation. UTF-8 with BOM so
    Excel renders Spanish accents. Prospect bio (age/height) and the prospect's
    latest rating fill in when the observation itself doesn't carry them."""
    prospects = {p.id: p for p in _prospects_of(session)}
    match = _match_label(session)
    date = _fmt_date(session.match_date or session.created_at)
    scout = session.scout_name or str(session.agent_chat_id)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for o in session.observations:
        p = prospects.get(o.prospect_id) if o.prospect_id else None
        rating = o.rating if o.rating is not None else (p.latest_rating if p else None)
        writer.writerow(
            [
                match,
                date,
                o.team or "",
                _opponent(session, o.team),
                o.player_name or (p.name if p else ""),
                o.player_number if o.player_number is not None else "",
                o.player_position or (p.position if p else ""),
                p.age if p and p.age is not None else "",
                p.height_cm if p and p.height_cm is not None else "",
                o.raw_quote,
                o.source or "",
                f"{rating:g}" if rating is not None else "",
                scout,
                o.created_at.isoformat() if o.created_at else "",
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def _prospects_of(session: Session) -> list[Prospect]:
    """The prospects referenced by this session's observations (prefetched if
    available; falls back to an empty list when not loaded)."""
    seen: dict[int, Prospect] = {}
    for o in session.observations:
        pr = getattr(o, "prospect", None)
        if pr is not None:
            seen[pr.id] = pr
    return list(seen.values())


# ── Markdown match report (REST API) ────────────────────────────────────────────
def build_markdown(session: Session) -> str:
    """Full markdown match report (served by the REST /report endpoint)."""
    out: list[str] = [f"# Informe del partido — {_match_label(session)}"]
    if session.label:
        out.append(f"*{session.label}*")
    out.append("")
    out.append(f"- Fecha: {_fmt_date(session.match_date or session.created_at)}")
    if session.competition:
        out.append(f"- Competición: {session.competition}")
    out.append(f"- Total de observaciones: {len(session.observations)}")
    out.append("")

    player_obs = [o for o in session.observations if not o.is_team_note]
    team_notes = [o for o in session.observations if o.is_team_note]

    out.append("## Jugadores")
    if not player_obs:
        out.append("_Sin observaciones de jugadores._")
    else:
        by_player: dict[tuple[str, str], list[Observation]] = defaultdict(list)
        for o in player_obs:
            key = (o.player_name or f"#{o.player_number}" or "?", o.team or "")
            by_player[key].append(o)
        for (name, team), obs in by_player.items():
            team_str = f" — {team}" if team else ""
            out.append(f"### {name}{team_str}")
            for o in obs:
                rating = f" (valoración {o.rating:g})" if o.rating is not None else ""
                out.append(f'- "{o.raw_quote}"{rating}')
            out.append("")

    out.append("## Notas de equipo")
    if team_notes:
        for o in team_notes:
            prefix = f"**{o.team}**: " if o.team else ""
            out.append(f"- {prefix}{o.raw_quote}")
    else:
        out.append("_Ninguna._")
    out.append("")
    return "\n".join(out)


# ── Accumulated cross-match player report ───────────────────────────────────────
def build_player_report(
    prospect: Prospect, observations: list[Observation], summary: str
) -> str:
    """Telegram-formatted accumulated player report: bio/decision header, raw
    cross-match observation history, then the AI-generated summary."""
    name = prospect.name or "Jugador sin nombre"
    lines = [f"👤 *{name}*"]
    bio = []
    if prospect.team:
        bio.append(prospect.team)
    if prospect.position:
        bio.append(prospect.position)
    if prospect.age is not None:
        bio.append(f"{prospect.age} años")
    if prospect.height_cm is not None:
        bio.append(f"{prospect.height_cm} cm")
    if bio:
        lines.append(" · ".join(bio))
    if prospect.latest_rating is not None:
        lines.append(f"Valoración actual: *{prospect.latest_rating:g}*")
    lines.append(f"Decisión: *{prospect.decision_status or 'Pendiente'}*")
    lines.append("")

    lines.append(f"*Historial* ({len(observations)} observaciones)")
    for o in observations:
        session = getattr(o, "session", None)
        match = _match_label(session) if session else ""
        date = _fmt_date(o.created_at)
        rating = f" — valoración {o.rating:g}" if o.rating is not None else ""
        src = f" [{o.source}]" if o.source else ""
        prefix = f"_{date} · {match}_: " if match else f"_{date}_: "
        lines.append(f'• {prefix}"{o.raw_quote}"{rating}{src}')
    lines.append("")

    lines.append("*Resumen*")
    lines.append(summary)
    return "\n".join(lines)
