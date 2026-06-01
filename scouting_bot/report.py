"""Post-match report generation.

Pure functions: given a hydrated Session, produce (a) a short Telegram summary
and (b) a full markdown report file. No Telegram or DB dependencies, so it is
trivially unit-testable.

Report contents follow the product outline:
  - Match header (teams, label, # observations)
  - Per-player rollup (sentiment counts, skills observed, notable quotes)
  - Highlighted target players
  - Team notes section (free-text)
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .models import HOME, Observation, Player, Session
from .taxonomy import SENTIMENT_NEGATIVE, SENTIMENT_POSITIVE, skill_label


def _player_label(p: Player) -> str:
    num = f"#{p.number}" if p.number is not None else "#?"
    pos = f", {p.position}" if p.position else ""
    star = " ⭐" if p.is_target else ""
    return f"{num} {p.name}{pos}{star}"


def _group_player_observations(
    session: Session,
) -> dict[int, list[Observation]]:
    grouped: dict[int, list[Observation]] = defaultdict(list)
    for obs in session.observations:
        if obs.player_id is not None:
            grouped[obs.player_id].append(obs)
    return grouped


def build_summary(session: Session) -> str:
    """Short summary shown directly in the Telegram chat."""
    grouped = _group_player_observations(session)
    team_notes = [o for o in session.observations if o.player_id is None]
    total = len(session.observations)

    lines = [
        f"📋 *Informe del partido*: {session.home_team} vs {session.away_team}",
    ]
    if session.label:
        lines.append(f"_{session.label}_")
    lines.append(f"Observaciones registradas: *{total}*")

    targets = [p for p in session.players if p.is_target]
    if targets:
        lines.append("")
        lines.append("⭐ *Jugadores objetivo*")
        for p in targets:
            obs = grouped.get(p.id or -1, [])
            pos = sum(1 for o in obs if o.sentiment == SENTIMENT_POSITIVE)
            neg = sum(1 for o in obs if o.sentiment == SENTIMENT_NEGATIVE)
            lines.append(f"• {_player_label(p)} — 👍 {pos} / 👎 {neg}")

    # Most-noted players (excluding zero-observation roster entries).
    noted = sorted(
        (p for p in session.players if grouped.get(p.id or -1)),
        key=lambda p: len(grouped[p.id or -1]),
        reverse=True,
    )
    if noted:
        lines.append("")
        lines.append("*Jugadores más observados*")
        for p in noted[:5]:
            obs = grouped[p.id or -1]
            pos = sum(1 for o in obs if o.sentiment == SENTIMENT_POSITIVE)
            neg = sum(1 for o in obs if o.sentiment == SENTIMENT_NEGATIVE)
            lines.append(f"• {_player_label(p)} — 👍 {pos} / 👎 {neg}")

    if team_notes:
        lines.append("")
        lines.append(f"📝 Notas de equipo: {len(team_notes)}")

    lines.append("")
    lines.append("Informe completo adjunto como archivo. 📎")
    return "\n".join(lines)


def build_markdown(session: Session) -> str:
    """Full downloadable markdown report."""
    players_by_id = {p.id: p for p in session.players}
    grouped = _group_player_observations(session)
    team_notes = [o for o in session.observations if o.player_id is None]

    out: list[str] = []
    out.append(f"# Informe del partido — {session.home_team} vs {session.away_team}")
    if session.label:
        out.append(f"*{session.label}*")
    out.append("")
    out.append(f"- ID de sesión: {session.id}")
    out.append(f"- Inicio: {session.created_at}")
    out.append(f"- Fin: {session.ended_at or '—'}")
    out.append(f"- Total de observaciones: {len(session.observations)}")
    out.append("")

    for side, team_name in ((HOME, session.home_team), ("away", session.away_team)):
        out.append(f"## {team_name} ({'Local' if side == HOME else 'Visitante'})")
        side_players = [p for p in session.players if p.side == side]
        if not side_players:
            out.append("_No se registró alineación._\n")
            continue
        for p in side_players:
            obs = grouped.get(p.id or -1, [])
            out.append(f"### {_player_label(p)}")
            if not obs:
                out.append("_Sin observaciones._\n")
                continue
            pos = sum(1 for o in obs if o.sentiment == SENTIMENT_POSITIVE)
            neg = sum(1 for o in obs if o.sentiment == SENTIMENT_NEGATIVE)
            skills = Counter(o.skill_category for o in obs if o.skill_category)
            out.append(f"- Valoración: 👍 {pos} positivas / 👎 {neg} negativas")
            if skills:
                skill_str = ", ".join(
                    f"{skill_label(s)} ({c})" for s, c in skills.most_common()
                )
                out.append(f"- Aspectos observados: {skill_str}")
            out.append("- Notas:")
            for o in obs:
                mark = "👍" if o.sentiment == SENTIMENT_POSITIVE else "👎"
                cat = f" _{skill_label(o.skill_category)}_" if o.skill_category else ""
                out.append(f"  - {mark}{cat}: \"{o.raw_quote}\"")
            out.append("")

    out.append("## Notas de equipo")
    if team_notes:
        for o in team_notes:
            label = session.home_team if o.side == HOME else session.away_team
            prefix = f"**{label}**: " if o.side else ""
            out.append(f"- {prefix}{o.raw_quote}")
    else:
        out.append("_Ninguna._")
    out.append("")

    return "\n".join(out)
