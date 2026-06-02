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


# ── PDF report ──────────────────────────────────────────────────────────────
# Built with ReportLab (pure-Python, no system deps). Emoji are intentionally
# replaced with text symbols (+N / −N, ★) so the PDF needs no special fonts and
# reads cleanly as a formal recruiter-facing document. Helvetica covers the
# accented Spanish characters used throughout.

_POS = "+"
_NEG = "−"  # U+2212 minus sign (renders better than a hyphen in Helvetica)
_STAR = "★"


def _pdf_player_label(p: Player) -> str:
    num = f"#{p.number}" if p.number is not None else "#?"
    pos = f", {p.position}" if p.position else ""
    star = f" {_STAR}" if p.is_target else ""
    return f"{num} {p.name}{pos}{star}"


def _esc(text: str) -> str:
    """Escape text for ReportLab Paragraph markup (it parses a mini-HTML)."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt_dt(value) -> str:
    """Human-friendly timestamp (drops microseconds): '2026-06-02 23:22 UTC'."""
    if value is None:
        return "—"
    try:
        return value.strftime("%Y-%m-%d %H:%M UTC")
    except AttributeError:
        return str(value)


def build_pdf(session: Session) -> bytes:
    """Full match report as PDF bytes (recruiter-facing document)."""
    # Imported lazily so the rest of the app (and tests) don't require reportlab
    # unless a PDF is actually generated.
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )
    import io

    grouped = _group_player_observations(session)
    team_notes = [o for o in session.observations if o.player_id is None]

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10,
                         textColor="#555555", spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=14,
                        spaceBefore=12, spaceAfter=6)
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=11,
                        spaceBefore=8, spaceAfter=2)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10,
                         leading=14, alignment=TA_LEFT)
    meta = ParagraphStyle("meta", parent=body, textColor="#444444")
    quote = ParagraphStyle("quote", parent=body, leftIndent=10 * mm, spaceAfter=2)

    story: list = []

    # Header
    story.append(Paragraph(
        f"Informe del partido — {_esc(session.home_team)} vs {_esc(session.away_team)}", h1
    ))
    if session.label:
        story.append(Paragraph(_esc(session.label), sub))
    story.append(Paragraph(f"ID de sesión: {session.id}", meta))
    story.append(Paragraph(f"Inicio: {_esc(_fmt_dt(session.created_at))}", meta))
    story.append(Paragraph(f"Fin: {_esc(_fmt_dt(session.ended_at))}", meta))
    story.append(Paragraph(
        f"Total de observaciones: {len(session.observations)}", meta
    ))
    story.append(Spacer(1, 6))

    # Per-team, per-player
    for side, team_name in ((HOME, session.home_team), ("away", session.away_team)):
        role = "Local" if side == HOME else "Visitante"
        story.append(Paragraph(f"{_esc(team_name)} ({role})", h2))
        side_players = [p for p in session.players if p.side == side]
        if not side_players:
            story.append(Paragraph("<i>No se registró alineación.</i>", body))
            continue
        for p in side_players:
            obs = grouped.get(p.id or -1, [])
            story.append(Paragraph(_esc(_pdf_player_label(p)), h3))
            if not obs:
                story.append(Paragraph("<i>Sin observaciones.</i>", body))
                continue
            pos = sum(1 for o in obs if o.sentiment == SENTIMENT_POSITIVE)
            neg = sum(1 for o in obs if o.sentiment == SENTIMENT_NEGATIVE)
            story.append(Paragraph(
                f"Valoración: {_POS}{pos} positivas / {_NEG}{neg} negativas", body
            ))
            skills = Counter(o.skill_category for o in obs if o.skill_category)
            if skills:
                skill_str = ", ".join(
                    f"{skill_label(s)} ({c})" for s, c in skills.most_common()
                )
                story.append(Paragraph(f"Aspectos observados: {_esc(skill_str)}", body))
            for o in obs:
                mark = _POS if o.sentiment == SENTIMENT_POSITIVE else _NEG
                cat = f" [{skill_label(o.skill_category)}]" if o.skill_category else ""
                story.append(Paragraph(
                    f"{mark}{_esc(cat)}: “{_esc(o.raw_quote)}”", quote
                ))

    # Team notes
    story.append(Paragraph("Notas de equipo", h2))
    if team_notes:
        for o in team_notes:
            label = session.home_team if o.side == HOME else session.away_team
            prefix = f"<b>{_esc(label)}</b>: " if o.side else ""
            story.append(Paragraph(f"{prefix}{_esc(o.raw_quote)}", body))
    else:
        story.append(Paragraph("<i>Ninguna.</i>", body))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Informe {session.home_team} vs {session.away_team}",
    )
    doc.build(story)
    return buf.getvalue()
