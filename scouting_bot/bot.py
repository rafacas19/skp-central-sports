"""Telegram bot — thin shell over ScoutingService.

Implements the conversation flows from the product outline:
  /nuevo → roster setup (image) → confirm → capture loop → /fin → report

Plus the edge flows: ambiguity prompts (inline buttons), corrections
(undo/reassign/flip), roster gaps (/addplayer), target flagging (/target),
resume-after-disconnect (state on disk), and an auto-nudge job.

All match state lives in SQLite, so an active session survives bot restarts and
the agent's phone dropping off — they just keep sending notes.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .ai import get_provider
from .config import settings
from .models import HOME, Session
from .report import build_csv, build_player_report, build_summary
from .service import ScoutingService
from .storage import Storage

logger = logging.getLogger(__name__)

# Per-chat transient state (user_data). All pending interactive states live in
# ONE FIFO list of typed entries so the team-ambiguity ask, future merge-confirm,
# etc. never collide. Each entry is a dict {"kind": ..., ...payload}.
_PENDING = "pending"


# ── helpers ─────────────────────────────────────────────────────────────
def _svc(context: ContextTypes.DEFAULT_TYPE) -> ScoutingService:
    return context.application.bot_data["service"]


# Short codes for decision buttons (callback_data has a tight length budget).
_DECISION_CODES = {
    "watch": "Seguir observando",
    "advance": "Avanzar",
    "discard": "Descartar",
}


def _decision_keyboard(prospect_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👀 Seguir observando", callback_data=f"decision:{prospect_id}:watch")],
            [InlineKeyboardButton("⬆️ Avanzar", callback_data=f"decision:{prospect_id}:advance")],
            [InlineKeyboardButton("🗑️ Descartar", callback_data=f"decision:{prospect_id}:discard")],
        ]
    )


# ── commands ────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚽ *Bot de scouting*\n\n"
        "Inicia un partido con:\n"
        "`/nuevo Equipo Local vs Equipo Visitante`\n\n"
        "Luego envía una foto de la alineación, confírmala y empieza a mandar notas "
        "de voz o texto. Finaliza con /fin.",
        parse_mode="Markdown",
    )


async def cmd_newmatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    arg = " ".join(context.args) if context.args else ""
    home, away, label, meta = _parse_match_metadata(arg)
    if not home or not away:
        await update.message.reply_text(
            "Uso: `/nuevo Equipo Local vs Equipo Visitante`\n"
            "Opcional: `| competición=Liga | fecha=2026-06-20 | sede=Estadio`\n"
            "Ejemplo: `/nuevo Millonarios vs América | competición=Liga`",
            parse_mode="Markdown",
        )
        return

    # No date given → assume today.
    if "match_date" not in meta:
        from datetime import datetime, timezone

        meta["match_date"] = datetime.now(timezone.utc)

    session, existing = await _svc(context).start_session(
        chat_id, home, away, label, **meta
    )
    if existing is not None:
        # One active match per scout: offer to close the current one first.
        context.user_data["pending_newmatch"] = (home, away, label, meta)
        await update.message.reply_text(
            f"⚠️ Ya tienes un partido activo: "
            f"*{existing.home_team} vs {existing.away_team}*.\n"
            "¿Quieres cerrarlo y empezar el nuevo?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Cerrar y empezar", callback_data="newmatch:close")],
                    [InlineKeyboardButton("🚫 Cancelar", callback_data="newmatch:cancel")],
                ]
            ),
        )
        return

    await update.message.reply_text(
        f"🆕 Partido iniciado: *{home} vs {away}*"
        + (f"\n_{label}_" if label else "")
        + "\n\nYa puedes enviar observaciones por texto o voz, p. ej. "
        "`América, #7, extremo, muy rápido en el 1vs1`. "
        "Finaliza con /finalizar.",
        parse_mode="Markdown",
    )


async def cmd_endmatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    svc = _svc(context)
    session = await svc.storage.get_active_session(chat_id)
    if session is None:
        await update.message.reply_text("No hay ninguna sesión activa. Inicia una con /nuevo.")
        return

    # Before finalizing, ask the scout to resolve likely-duplicate players
    # (e.g. "Castro" vs "Castro B." captured as two prospects). Only then build
    # and send the report. With no candidate pairs, finalize immediately.
    pairs = await svc.find_dedup_pairs(session)
    if not pairs:
        await _do_finalize(chat_id, context, session)
        return

    pending = context.user_data.setdefault(_PENDING, [])
    for keep, drop in pairs:
        pending.append(
            {
                "kind": "dedup",
                "keep_id": keep.id,
                "drop_id": drop.id,
                "keep_name": keep.name,
                "drop_name": drop.name,
            }
        )
    pending.append({"kind": "finalize_after_dedup", "session_id": session.id})
    await _ask_next_pending(chat_id, context)


async def _do_finalize(
    chat_id: int, context: ContextTypes.DEFAULT_TYPE, session: Session
) -> None:
    """End the session and send the summary + CSV. Uses context.bot.send_* so it
    works both from /finalizar directly and after the dedup-confirmation flow
    (where there is no incoming message to reply to)."""
    svc = _svc(context)
    ended = await svc.end_session(session)
    await context.bot.send_message(chat_id, "🏁 Sesión finalizada. Generando informe…")
    await context.bot.send_message(
        chat_id, build_summary(ended), parse_mode="Markdown"
    )
    csv_bytes = build_csv(ended)
    buf = io.BytesIO(csv_bytes)
    fname = f"informe_{ended.home_team}_vs_{ended.away_team}".replace(" ", "_")
    await context.bot.send_document(
        chat_id, document=InputFile(buf, filename=f"{fname}.csv")
    )


async def cmd_set_scout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/yo <nombre> — set the scout's display name (per chat), used in reports."""
    chat_id = update.effective_chat.id
    svc = _svc(context)
    name = " ".join(context.args).strip() if context.args else ""
    if not name:
        current = await svc.storage.get_scout_name(chat_id)
        await update.message.reply_text(
            f"Tu nombre de scout: *{current}*." if current
            else "Uso: `/yo <tu nombre>` para identificarte en los informes.",
            parse_mode="Markdown",
        )
        return
    await svc.storage.set_scout_name(chat_id, name)
    # If a match is active, stamp the name onto it too.
    session = await svc.storage.get_active_session(chat_id)
    if session is not None:
        await svc.storage.update_session_meta(session.id, scout_name=name)
    await update.message.reply_text(f"👤 Hola, *{name}*. Te identificaré en los informes.", parse_mode="Markdown")


async def cmd_team_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/equipo [local|visitante] <texto> — a team-level tactical note."""
    session = await _require_session(update, context)
    if session is None:
        return
    raw = update.message.text or ""
    body = raw.split(maxsplit=1)[1].strip() if " " in raw.strip() else ""
    if not body:
        await update.message.reply_text(
            "Uso: `/equipo <nota táctica>` (opcional: empieza con local/visitante).",
            parse_mode="Markdown",
        )
        return
    team = None
    first = body.split(maxsplit=1)
    if first and first[0].lower() in ("local", "visitante", "home", "away"):
        side = _parse_side(first[0])
        team = session.home_team if side == HOME else session.away_team
        body = first[1] if len(first) > 1 else ""
    await _svc(context).add_team_note(session, body, team)
    await update.message.reply_text("📝 Nota de equipo registrada.")


async def cmd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/valorar <nombre> <nota> — manual rating (1–10, decimales permitidos)."""
    session = await _require_session(update, context)
    if session is None:
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: `/valorar <nombre> <nota>` — p. ej. `/valorar Castro 7.5`.",
            parse_mode="Markdown",
        )
        return
    score_raw = args[-1].replace(",", ".")
    name = " ".join(args[:-1])
    try:
        score = float(score_raw)
    except ValueError:
        await update.message.reply_text("La nota debe ser un número (1–10).")
        return
    if not (1.0 <= score <= 10.0):
        await update.message.reply_text("La nota debe estar entre 1 y 10.")
        return
    result = await _svc(context).rate_by_name(session.agent_chat_id, name, score)
    if isinstance(result, list):
        if not result:
            await update.message.reply_text(f"No tengo a ningún jugador llamado *{name}*.", parse_mode="Markdown")
        else:
            names = ", ".join(p.name for p in result)
            await update.message.reply_text(
                f"Hay varios jugadores que coinciden ({names}). Sé más específico."
            )
        return
    await update.message.reply_text(
        f"⭐ *{result.name}*: valoración {score:g}.", parse_mode="Markdown"
    )


async def cmd_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/foto — register an unknown player by photo. Asks for the photo next."""
    session = await _require_session(update, context)
    if session is None:
        return
    context.user_data["awaiting_photo_obs"] = True
    await update.message.reply_text(
        "📸 Envía la foto del jugador. Luego mándame una observación y crearé un "
        "perfil temporal que podrás editar más tarde."
    )


async def cmd_player_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reporte_jugador <nombre> — accumulated cross-match report + AI summary."""
    chat_id = update.effective_chat.id
    svc = _svc(context)
    name = " ".join(context.args).strip() if context.args else ""
    if not name:
        await update.message.reply_text(
            "Uso: `/reporte_jugador <nombre>`.", parse_mode="Markdown"
        )
        return

    result = await svc.player_report(chat_id, name)
    if isinstance(result, list):
        if not result:
            await update.message.reply_text(
                f"No tengo observaciones de *{name}*.", parse_mode="Markdown"
            )
        else:
            names = ", ".join(p.name for p in result)
            await update.message.reply_text(
                f"Hay varios jugadores que coinciden ({names}). Sé más específico."
            )
        return

    prospect, observations, summary = result
    await update.message.reply_text(
        build_player_report(prospect, observations, summary),
        parse_mode="Markdown",
        reply_markup=_decision_keyboard(prospect.id),
    )


# Accept short forms for /decision.
_DECISION_ALIASES = {
    "pendiente": "Pendiente",
    "seguir": "Seguir observando",
    "observar": "Seguir observando",
    "avanzar": "Avanzar",
    "descartar": "Descartar",
}


async def cmd_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/decision <nombre> <estado> — Pendiente | Seguir observando | Avanzar | Descartar."""
    chat_id = update.effective_chat.id
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: `/decision <nombre> <pendiente|seguir|avanzar|descartar>`.",
            parse_mode="Markdown",
        )
        return
    status_word = args[-1].lower()
    status = _DECISION_ALIASES.get(status_word)
    if status is None and " ".join(args[-2:]).lower() == "seguir observando":
        status = "Seguir observando"
        name = " ".join(args[:-2])
    else:
        name = " ".join(args[:-1])
    if status is None:
        await update.message.reply_text(
            "Estado no válido. Usa: pendiente, seguir, avanzar o descartar."
        )
        return
    result = await _svc(context).set_decision_by_name(chat_id, name, status)
    if isinstance(result, list):
        msg = (
            f"No tengo a *{name}*." if not result
            else "Hay varios jugadores que coinciden. Sé más específico."
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    await update.message.reply_text(
        f"✅ {result.name}: decisión *{status}*.", parse_mode="Markdown"
    )


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/editar <nombre> campo=valor … — edit a player (nombre/equipo/posicion/
    numero/edad/altura/nota). Naming a temporary player may trigger a merge ask."""
    chat_id = update.effective_chat.id
    raw = update.message.text or ""
    body = raw.split(maxsplit=1)[1].strip() if " " in raw.strip() else ""
    name, fields = _parse_edit(body)
    if not name or not fields:
        await update.message.reply_text(
            "Uso: `/editar <nombre> campo=valor` — campos: nombre, equipo, "
            "posicion, numero, edad, altura, nota.",
            parse_mode="Markdown",
        )
        return

    svc = _svc(context)
    result = await svc.edit_prospect(chat_id, name, fields)
    if isinstance(result, list):
        msg = (
            f"No encontré a *{name}*." if not result
            else "Hay varios jugadores que coinciden. Sé más específico."
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # If we just gave a name, check for a likely duplicate and ask before merging.
    if "name" in fields:
        dup = await svc.detect_duplicate(chat_id, result.name, result.team, result.id)
        if dup is not None:
            context.user_data[_PENDING] = context.user_data.get(_PENDING, [])
            await update.message.reply_text(
                f"Encontré un jugador parecido:\n\n"
                f"*{dup.name}* - {dup.team or '?'} - {dup.position or '?'}\n\n"
                f"¿Es el mismo jugador que *{result.name}*?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("✅ Sí, unir", callback_data=f"merge:{dup.id}:{result.id}")],
                        [InlineKeyboardButton("🚫 No, crear nuevo", callback_data="merge:cancel")],
                    ]
                ),
            )
            return
    await update.message.reply_text(f"✏️ *{result.name}* actualizado.", parse_mode="Markdown")


async def cmd_merge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unir <nombre1> | <nombre2> — merge two player records (asks to confirm)."""
    chat_id = update.effective_chat.id
    raw = update.message.text or ""
    body = raw.split(maxsplit=1)[1] if " " in raw.strip() else ""
    if "|" not in body:
        await update.message.reply_text(
            "Uso: `/unir <nombre1> | <nombre2>`.", parse_mode="Markdown"
        )
        return
    n1, n2 = (s.strip() for s in body.split("|", 1))
    svc = _svc(context)
    m1 = await svc.storage.find_prospects_by_name(chat_id, n1)
    m2 = await svc.storage.find_prospects_by_name(chat_id, n2)
    if len(m1) != 1 or len(m2) != 1:
        await update.message.reply_text(
            "No pude identificar a ambos jugadores sin ambigüedad."
        )
        return
    keep, drop = m1[0], m2[0]
    await update.message.reply_text(
        f"¿Unir *{drop.name}* en *{keep.name}*? Se conservará *{keep.name}*.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Sí, unir", callback_data=f"merge:{keep.id}:{drop.id}")],
                [InlineKeyboardButton("🚫 Cancelar", callback_data="merge:cancel")],
            ]
        ),
    )


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    removed = await _svc(context).undo_last(session)
    if removed is None:
        await update.message.reply_text("No hay nada que deshacer.")
    else:
        await update.message.reply_text(f"↩️ Última nota eliminada: \"{removed.raw_quote}\"")


# ── media + text capture ────────────────────────────────────────────────
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo. Only meaningful right after /foto: it creates a temporary unknown
    player; the NEXT observation attaches to them. A bare photo (no pending /foto)
    gets a gentle nudge — there is no lineup pre-seeding."""
    session = await _require_session(update, context)
    if session is None:
        return
    if not context.user_data.get("awaiting_photo_obs"):
        await update.message.reply_text(
            "📸 Para registrar a un jugador desconocido por foto, primero envía /foto."
        )
        return

    context.user_data.pop("awaiting_photo_obs", None)
    file_id = update.message.photo[-1].file_id
    prospect = await _svc(context).attach_photo(session, None, file_id)
    context.user_data["photo_prospect_id"] = prospect.id
    await update.message.reply_text(
        "✅ Foto guardada. Ahora envía la observación de este jugador."
    )


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_session(update, context)
    if session is None:
        return
    svc = _svc(context)
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    voice = update.message.voice or update.message.audio
    tg_file = await voice.get_file()
    audio_bytes = bytes(await tg_file.download_as_bytearray())
    text = await svc.transcribe(audio_bytes, "audio/ogg")
    await _handle_capture(update, context, session, text, source="voice")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text → an observation. No lineup gate: capture starts immediately."""
    session = await _require_session(update, context)
    if session is None:
        return
    await _handle_capture(update, context, session, update.message.text.strip())


async def _handle_capture(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: Session,
    text: str,
    source: str = "text",
) -> None:
    svc = _svc(context)

    # If a /foto profile is awaiting its observation, attach this one to it and
    # don't run identity resolution (the player is unknown by design).
    photo_pid = context.user_data.pop("photo_prospect_id", None)
    if photo_pid is not None:
        prospect = await svc.storage.get_prospect(photo_pid)
        if prospect is not None:
            await svc.capture_to_prospect(session, text, prospect, source=source)
            await update.message.reply_text(
                "✅ Observación guardada para el jugador de la foto "
                "(perfil temporal — edítalo con /editar cuando sepas quién es)."
            )
            return

    results = await svc.capture_notes(session, text, source=source)

    if not results:
        await update.message.reply_text(
            "🤔 No pude identificar ninguna observación en ese mensaje. "
            "Inténtalo de nuevo, mencionando al jugador por nombre o número."
        )
        return

    # Stored notes (player or team) — acknowledge the count.
    stored = [r for r in results if r.observation is not None]
    if stored:
        n = len(stored)
        if n == 1 and stored[0].classified.is_team_note:
            await update.message.reply_text(
                "📝 ✅", reply_to_message_id=update.message.message_id
            )
        elif n == 1:
            await update.message.reply_text("✅")
        else:
            await update.message.reply_text(f"✅ {n} notas registradas")

    # Number-only notes with no team → queue a team-choice ask (don't guess).
    pending = context.user_data.setdefault(_PENDING, [])
    was_empty = not pending
    for r in results:
        if r.needs_team_choice:
            pending.append(
                {
                    "kind": "team_ambiguity",
                    "classified": r.classified,
                    "teams": r.team_candidates,
                    "source": source,
                }
            )
    if was_empty and pending:
        await _ask_next_pending(update.effective_chat.id, context)


async def _ask_next_pending(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt for the pending interactive state at the head of the FIFO list.

    Sends a fresh message; leaves the head in place until it's answered."""
    pending = context.user_data.get(_PENDING, [])
    if not pending:
        return
    entry = pending[0]
    if entry["kind"] == "team_ambiguity":
        classified = entry["classified"]
        number = classified.player_ref.number if classified.player_ref else "?"
        teams = entry["teams"]
        rows = [
            [InlineKeyboardButton(f"#{number} de {t}", callback_data=f"team:{i}")]
            for i, t in enumerate(teams)
        ]
        rows.append([InlineKeyboardButton("🚫 Omitir", callback_data="team:skip")])
        await context.bot.send_message(
            chat_id,
            f"🤔 ¿Te refieres al #{number} de {teams[0]} o al #{number} de {teams[1]}?",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif entry["kind"] == "dedup":
        # Skip if a prior merge already removed one side (transitive chains).
        svc = _svc(context)
        keep = await svc.storage.get_prospect(entry["keep_id"])
        drop = await svc.storage.get_prospect(entry["drop_id"])
        if keep is None or drop is None:
            pending.pop(0)
            await _ask_next_pending(chat_id, context)
            return
        # Two "Sí" buttons let the scout choose which NAME survives; the merge
        # repoints the other's observations onto it.
        rows = [
            [InlineKeyboardButton(
                f"✅ Sí — mantener «{entry['keep_name']}»",
                callback_data=f"dedup:{entry['keep_id']}:{entry['drop_id']}",
            )],
            [InlineKeyboardButton(
                f"✅ Sí — mantener «{entry['drop_name']}»",
                callback_data=f"dedup:{entry['drop_id']}:{entry['keep_id']}",
            )],
            [InlineKeyboardButton("🚫 No, son distintos", callback_data="dedup:no")],
        ]
        await context.bot.send_message(
            chat_id,
            f"🤔 ¿*{entry['keep_name']}* y *{entry['drop_name']}* son el mismo jugador?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif entry["kind"] == "finalize_after_dedup":
        pending.pop(0)
        if not pending:
            context.user_data.pop(_PENDING, None)
        svc = _svc(context)
        session = await svc.storage.get_active_session(chat_id)
        if session is not None and session.id == entry["session_id"]:
            await _do_finalize(chat_id, context, session)


# ── callbacks (inline buttons) ───────────────────────────────────────────
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    svc = _svc(context)
    chat_id = update.effective_chat.id

    # Decision / merge / dedup buttons don't need an active-match lookup here.
    if data.startswith("decision:"):
        await _resolve_decision(query, context, data)
        return
    if data.startswith("merge:"):
        await _resolve_merge(query, context, data)
        return
    if data.startswith("dedup:"):
        await _resolve_dedup(query, context, data)
        return

    session = await svc.storage.get_active_session(chat_id)
    if session is None:
        await query.edit_message_text("La sesión ya no está activa.")
        return

    if data.startswith("team:"):
        await _resolve_team_choice(query, context, session, data.split(":", 1)[1])
    elif data.startswith("newmatch:"):
        await _resolve_newmatch(query, context, session, data.split(":", 1)[1])


async def _resolve_decision(query, context, data: str) -> None:
    """Handle a decision button: 'decision:<prospect_id>:<code>'."""
    _, pid, code = data.split(":", 2)
    status = _DECISION_CODES.get(code)
    if status is None:
        return
    await _svc(context).set_decision_by_id(int(pid), status)
    await query.edit_message_text(f"✅ Decisión registrada: {status}.")


async def _resolve_merge(query, context, data: str) -> None:
    """Handle a merge button: 'merge:<keep_id>:<drop_id>' or 'merge:cancel'."""
    parts = data.split(":")
    if parts[1] == "cancel":
        await query.edit_message_text("Ok, los mantengo como jugadores distintos.")
        return
    keep_id, drop_id = int(parts[1]), int(parts[2])
    await _svc(context).merge(keep_id, drop_id)
    await query.edit_message_text("🔗 Jugadores unidos.")


async def _resolve_dedup(query, context, data: str) -> None:
    """Handle an end-of-match dedup answer: 'dedup:<keep_id>:<drop_id>' (Sí, with
    the chosen surviving name first) or 'dedup:no' (son distintos). Then advance
    the pending queue — the next dedup question, or the finalize sentinel."""
    pending = context.user_data.get(_PENDING, [])
    parts = data.split(":")
    if parts[1] == "no":
        await query.edit_message_text("Ok, los mantengo como jugadores distintos.")
    else:
        keep_id, drop_id = int(parts[1]), int(parts[2])
        await _svc(context).merge(keep_id, drop_id)
        await query.edit_message_text("🔗 Jugadores unidos.")

    # Pop the answered dedup head and drive the next pending step (which may be
    # the finalize sentinel → builds + sends the report).
    if pending and pending[0]["kind"] == "dedup":
        pending.pop(0)
    if pending:
        await _ask_next_pending(query.message.chat_id, context)
    else:
        context.user_data.pop(_PENDING, None)


async def _resolve_newmatch(query, context, session: Session, action: str) -> None:
    """Resolve the 'close current match and start a new one?' prompt."""
    svc = _svc(context)
    pending = context.user_data.pop("pending_newmatch", None)
    if action == "cancel" or pending is None:
        await query.edit_message_text("Ok, sigo con el partido actual.")
        return
    home, away, label, meta = pending
    await svc.end_session(session)
    new_session, _ = await svc.start_session(
        session.agent_chat_id, home, away, label, **meta
    )
    await query.edit_message_text(
        f"🏁 Partido anterior cerrado.\n🆕 Partido iniciado: {home} vs {away}. "
        "Ya puedes enviar observaciones."
    )


async def _resolve_team_choice(query, context, session: Session, picked: str) -> None:
    pending: list = context.user_data.get(_PENDING, [])
    entry = pending[0] if pending and pending[0]["kind"] == "team_ambiguity" else None
    if entry is None:
        await query.edit_message_text("No hay nada pendiente.")
        return

    if picked == "skip":
        await query.edit_message_text("🚫 Omitida — nota descartada.")
    else:
        team = entry["teams"][int(picked)]
        await _svc(context).resolve_team_choice(
            session, entry["classified"], team, source=entry.get("source", "text")
        )
        await query.edit_message_text(f"✅ Registrada para {team}.")

    pending.pop(0)
    if pending:
        await _ask_next_pending(query.message.chat_id, context)
    else:
        context.user_data.pop(_PENDING, None)


# ── auto-nudge job ────────────────────────────────────────────────────────
async def nudge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nudge agents who left a session open with no recent activity."""
    svc = _svc(context)
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.session_nudge_minutes
    )
    for session in await svc.storage.stale_active_sessions(cutoff):
        try:
            await context.bot.send_message(
                session.agent_chat_id,
                f"⏳ ¿Sigues observando *{session.home_team} vs {session.away_team}*? "
                "Envía una nota para continuar, o /fin para finalizar.",
                parse_mode="Markdown",
            )
            # Touch so we don't nudge again immediately.
            await svc.storage.touch_session(session.id)
        except Exception:  # noqa: BLE001 — best-effort nudge
            logger.exception("nudge failed for session %s", session.id)


# ── small utilities ────────────────────────────────────────────────────────
async def _require_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Session | None:
    chat_id = update.effective_chat.id
    session = await _svc(context).storage.get_active_session(chat_id)
    if session is None and update.message:
        # Don't block — guide the agent.
        await update.message.reply_text(
            "No hay ninguna sesión activa. "
            "Inicia una con `/nuevo Local vs Visitante`.",
        )
    return session


def _parse_side(raw: str) -> str:
    """Map a side keyword (ES or EN) onto the internal HOME/'away' value."""
    key = raw.strip().lower()
    if key.startswith("v") or key.startswith("a"):  # visitante / away
        return "away"
    return HOME  # local / home (default)


# Optional `| campo=valor` metadata on /nuevo. Keys are accent/case-insensitive.
_META_KEYS = {
    "competicion": "competition", "competición": "competition", "liga": "competition",
    "categoria": "category", "categoría": "category",
    "sede": "location", "estadio": "location", "lugar": "location",
    "fecha": "match_date", "date": "match_date",
}


def _parse_match_metadata(arg: str) -> tuple[str, str, str | None, dict]:
    """Parse `/nuevo A vs B | competición=Liga | fecha=2026-06-20 | sede=X`.

    Returns (home, away, label, metadata). Segments with `key=value` populate
    metadata (competition/category/location/match_date); a plain segment with no
    '=' is kept as the free-text label. Unparseable date → ignored."""
    segments = [s.strip() for s in arg.split("|")]
    head = segments[0]
    parts = head.split(" vs ") if " vs " in head else head.split(" - ")
    if len(parts) != 2:
        return "", "", None, {}
    home, away = parts[0].strip(), parts[1].strip()

    label = None
    meta: dict = {}
    for seg in segments[1:]:
        if not seg:
            continue
        if "=" in seg:
            key, _, value = seg.partition("=")
            field = _META_KEYS.get(key.strip().lower())
            value = value.strip()
            if field == "match_date":
                dt = _parse_date(value)
                if dt is not None:
                    meta["match_date"] = dt
            elif field and value:
                meta[field] = value
        elif label is None:
            label = seg or None
    return home, away, label, meta


def _parse_date(value: str):
    """Parse a YYYY-MM-DD (or DD/MM/YYYY) date to an aware datetime, else None."""
    from datetime import datetime, timezone

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# /editar field aliases → Prospect attribute names.
_EDIT_FIELDS = {
    "nombre": "name", "name": "name",
    "equipo": "team", "team": "team",
    "posicion": "position", "posición": "position", "position": "position",
    "numero": "_number_ignored", "número": "_number_ignored",  # number lives on obs, not prospect
    "edad": "age", "age": "age",
    "altura": "height_cm", "height": "height_cm",
    "nota": "notes", "notes": "notes",
}
_EDIT_RE = re.compile(r"(\w+)\s*=\s*([^=]+?)(?=\s+\w+\s*=|$)")


def _parse_edit(body: str) -> tuple[str, dict]:
    """Parse '<name> campo=valor campo=valor' → (name, {attr: value}).

    The name is everything before the first 'campo='. Integer fields (edad,
    altura) are coerced; unknown / number fields are ignored."""
    first = _EDIT_RE.search(body)
    if not first:
        return body.strip(), {}
    name = body[: first.start()].strip()
    fields: dict = {}
    for key, value in _EDIT_RE.findall(body):
        attr = _EDIT_FIELDS.get(key.strip().lower())
        value = value.strip()
        if attr is None or attr == "_number_ignored" or not value:
            continue
        if attr in ("age", "height_cm"):
            try:
                fields[attr] = int(value)
            except ValueError:
                continue
        else:
            fields[attr] = value
    return name, fields


# ── error handler ────────────────────────────────────────────────────────
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any exception raised while handling an update.

    Without this, a handler exception (e.g. an AI call failing) is swallowed by
    PTB — the webhook still returns 200 and the user just sees silence, with no
    trace in the logs. This makes that failure visible and tells the user.
    """
    logger.exception(
        "Error handling update %s",
        getattr(update, "update_id", update),
        exc_info=context.error,
    )
    # Best-effort: let the agent know something went wrong rather than ghosting.
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Ups, algo falló procesando ese mensaje. Inténtalo de nuevo; "
                "si persiste, avisa al administrador."
            )
    except Exception:  # noqa: BLE001 — never let the error handler itself raise
        logger.exception("failed to notify user about the error")


# ── app factory ────────────────────────────────────────────────────────────
def register_handlers(app: Application) -> None:
    """Register every command/message/callback handler + the error handler.

    Extracted so the E2E test harness builds an Application with exactly the same
    handler set as production, without the wiring drifting between them. The job
    queue (auto-nudge) is intentionally NOT registered here — only the request
    handlers — so tests don't spawn the periodic nudge job.
    """
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    # Primary command names are Spanish; English names kept as hidden aliases.
    app.add_handler(CommandHandler(["nuevo", "newmatch"], cmd_newmatch))
    app.add_handler(CommandHandler(["finalizar", "fin", "endmatch"], cmd_endmatch))
    app.add_handler(CommandHandler("yo", cmd_set_scout))
    app.add_handler(CommandHandler(["equipo", "team"], cmd_team_note))
    app.add_handler(CommandHandler(["valorar", "rate"], cmd_rate))
    app.add_handler(CommandHandler(["foto", "photo"], cmd_photo))
    app.add_handler(CommandHandler(["reporte_jugador", "playerreport"], cmd_player_report))
    app.add_handler(CommandHandler(["decision", "decidir"], cmd_decision))
    app.add_handler(CommandHandler(["editar", "edit"], cmd_edit))
    app.add_handler(CommandHandler(["unir", "merge"], cmd_merge))
    app.add_handler(CommandHandler("undo", cmd_undo))

    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))

    # Surface (don't swallow) exceptions raised inside any handler.
    app.add_error_handler(on_error)


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
    # Fail fast if live-AI mode is selected without the required keys.
    settings.require_ai_keys()

    # Storage is stateless (Tortoise manages the connection pool globally),
    # so it's safe to construct here without opening anything.
    storage = Storage()
    ai = get_provider()
    service = ScoutingService(storage, ai, settings.confidence_threshold)

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["service"] = service

    register_handlers(app)

    if app.job_queue is not None:
        app.job_queue.run_repeating(nudge_job, interval=600, first=600)

    return app
