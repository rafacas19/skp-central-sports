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
from .models import HOME, Player, Session
from .report import build_csv, build_summary
from .service import ScoutingService
from .storage import Storage
from .taxonomy import SENTIMENT_POSITIVE

logger = logging.getLogger(__name__)

# Keys for per-chat transient state (bot_data / user_data).
# One message can produce several ambiguous notes, so we hold a QUEUE of them and
# resolve one at a time: a list of (ClassifiedNote, list[Player]) tuples.
_PENDING_QUEUE = "pending_disambiguation_queue"


# ── helpers ─────────────────────────────────────────────────────────────
def _svc(context: ContextTypes.DEFAULT_TYPE) -> ScoutingService:
    return context.application.bot_data["service"]


def _roster_text(session: Session) -> str:
    lines = ["*Alineación detectada — por favor confirma:*", ""]
    for side, name in ((HOME, session.home_team), ("away", session.away_team)):
        lines.append(f"*{name}* ({'Local' if side == HOME else 'Visitante'})")
        for p in session.players:
            if p.side == side:
                num = f"#{p.number}" if p.number is not None else "#?"
                pos = f" ({p.position})" if p.position else ""
                lines.append(f"  {num} {p.name}{pos}")
        lines.append("")
    lines.append("Pulsa *Confirmar* si es correcta. Si falta un equipo, envía otra")
    lines.append("foto y se añadirá. También puedes corregir por texto")
    lines.append("(p. ej. `#10 es Pérez` o `visitante #8 posición CM`).")
    return "\n".join(lines)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Confirmar alineación", callback_data="confirm_roster")]]
    )


def _candidate_keyboard(candidates: list[Player]) -> InlineKeyboardMarkup:
    rows = []
    for p in candidates[:8]:
        side = "🏠" if p.side == HOME else "🚩"
        num = f"#{p.number}" if p.number is not None else "#?"
        label = f"{side} {num} {p.name}"
        rows.append([InlineKeyboardButton(label, callback_data=f"pick:{p.id}")])
    rows.append([InlineKeyboardButton("🚫 Omitir / no está en la lista", callback_data="pick:skip")])
    return InlineKeyboardMarkup(rows)


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
    home, away, label = _parse_match_arg(arg)
    if not home or not away:
        await update.message.reply_text(
            "Uso: `/nuevo Equipo Local vs Equipo Visitante [| etiqueta]`\n"
            "Ejemplo: `/nuevo Boca vs River | Liga, jornada 12`",
            parse_mode="Markdown",
        )
        return

    session, existing = await _svc(context).start_session(chat_id, home, away, label)
    if existing is not None:
        await update.message.reply_text(
            f"⚠️ Ya tienes una sesión activa: "
            f"*{existing.home_team} vs {existing.away_team}*.\n"
            "Finalízala con /fin antes de iniciar otra.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        f"🆕 Sesión iniciada: *{home} vs {away}*"
        + (f"\n_{label}_" if label else "")
        + "\n\n📸 Ahora envía una *foto de la alineación* (puedes mandar una por "
        "equipo). O, si solo quieres seguir a algún jugador, escríbelos con "
        "`/jugadores local: 10 Messi DC, 7 Di María; visitante: 5 Ramos`.",
        parse_mode="Markdown",
    )


async def cmd_endmatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    svc = _svc(context)
    session = await svc.storage.get_active_session(chat_id)
    if session is None:
        await update.message.reply_text("No hay ninguna sesión activa. Inicia una con /nuevo.")
        return

    ended = await svc.end_session(session)
    await update.message.reply_text("🏁 Sesión finalizada. Generando informe…")

    summary = build_summary(ended)
    await update.message.reply_text(summary, parse_mode="Markdown")

    csv_bytes = build_csv(ended)
    buf = io.BytesIO(csv_bytes)
    fname = f"informe_{ended.home_team}_vs_{ended.away_team}".replace(" ", "_")
    await update.message.reply_document(
        document=InputFile(buf, filename=f"{fname}.csv")
    )


async def cmd_manual_lineup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual lineup: stage a partial roster from a free-text list, e.g.

        /jugadores local: 10 Messi DC, 7 Di María; visitante: 5 Ramos

    For when the scout only wants to watch one or two players, not the whole
    team. Players are staged (appended) and the scout can confirm a partial
    roster — even a single player — then start capturing.
    """
    session = await _require_session(update, context)
    if session is None:
        return
    # Parse from the raw text, not context.args: arg-splitting on spaces would
    # break multi-word names ("Di María").
    raw = update.message.text or ""
    raw = raw.split(maxsplit=1)[1] if " " in raw.strip() else ""
    entries = _parse_manual_lineup(raw)
    if not entries:
        await update.message.reply_text(
            "Uso: `/jugadores local: 10 Messi DC, 7 Di María; visitante: 5 Ramos`\n"
            "El número y la posición son opcionales (`local: Messi`).",
            parse_mode="Markdown",
        )
        return

    svc = _svc(context)
    for side, number, name, position in entries:
        await svc.add_missing_player(session, side, number, name, position)
    session = await svc.storage.get_session(session.id)
    await update.message.reply_text(
        _roster_text(session), parse_mode="Markdown", reply_markup=_confirm_keyboard()
    )


async def cmd_addplayer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Roster gap: /addplayer home 14 Gómez CB"""
    session = await _require_session(update, context)
    if session is None:
        return
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "Uso: `/addplayer <local|visitante> <número> <nombre> [posición]`",
            parse_mode="Markdown",
        )
        return
    side = _parse_side(args[0])
    try:
        number = int(args[1])
    except ValueError:
        await update.message.reply_text("El número debe ser un entero.")
        return
    name = args[2]
    position = args[3] if len(args) > 3 else None
    await _svc(context).add_missing_player(session, side, number, name, position)
    await update.message.reply_text(f"➕ Añadido {_side_label(side)} #{number} {name}.")


async def cmd_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Flag a target player: /target home 10"""
    session = await _require_session(update, context)
    if session is None:
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: `/target <local|visitante> <número>`", parse_mode="Markdown"
        )
        return
    side = _parse_side(args[0])
    try:
        number = int(args[1])
    except ValueError:
        await update.message.reply_text("El número debe ser un entero.")
        return
    match = [p for p in session.players if p.side == side and p.number == number]
    if not match:
        await update.message.reply_text("Ese jugador no está en la alineación.")
        return
    await _svc(context).set_target(match[0], True)
    await update.message.reply_text(
        f"⭐ {_side_label(side)} #{number} {match[0].name} marcado como objetivo."
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
    """Lineup image → parse → MERGE into the staged roster → show confirm keyboard.

    A lineup may arrive across several photos (one team each). Each photo before
    confirmation is merged into what's already staged, so a second photo adds the
    other team instead of wiping the first.
    """
    session = await _require_session(update, context)
    if session is None:
        return
    svc = _svc(context)

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    image_bytes = bytes(await tg_file.download_as_bytearray())

    parsed = await svc.parse_and_stage_roster(image_bytes, "image/jpeg")
    if not parsed:
        await update.message.reply_text(
            "No pude leer ningún jugador en esa imagen. Prueba con una foto más "
            "nítida, o añade jugadores manualmente con /addplayer."
        )
        return
    await svc.merge_roster(session, parsed)
    session = await svc.storage.get_session(session.id)
    await update.message.reply_text(
        _roster_text(session), parse_mode="Markdown", reply_markup=_confirm_keyboard()
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
    await _handle_capture(update, context, session, text)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text: either a roster correction (pre-confirm) or an observation."""
    session = await _require_session(update, context)
    if session is None:
        return
    text = update.message.text.strip()

    # Before roster is confirmed, treat text as a roster correction note.
    if not session.roster_confirmed:
        await _apply_roster_correction(update, context, session, text)
        return

    await _handle_capture(update, context, session, text)


async def _handle_capture(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: Session,
    text: str,
) -> None:
    svc = _svc(context)
    results = await svc.capture_notes(session, text)

    if not results:
        await update.message.reply_text(
            "🤔 No pude identificar ninguna observación en ese mensaje. "
            "Inténtalo de nuevo, mencionando al jugador por nombre o número."
        )
        return

    # Confident notes (player or team) are already stored — acknowledge the count.
    stored = [r for r in results if not r.needs_disambiguation]
    if stored:
        n = len(stored)
        if n == 1 and stored[0].classified.is_team_note:
            await update.message.reply_text(
                "📝 ✅", reply_to_message_id=update.message.message_id
            )
        elif n == 1:
            obs = stored[0].observation
            mark = "👍" if obs and obs.sentiment == SENTIMENT_POSITIVE else "👎"
            await update.message.reply_text("✅" + mark)
        else:
            await update.message.reply_text(f"✅ {n} notas registradas")

    # Ambiguous notes are queued and asked one at a time (only when unsure).
    ambiguous = [(r.classified, r.candidates) for r in results if r.needs_disambiguation]
    if ambiguous:
        queue = context.user_data.setdefault(_PENDING_QUEUE, [])
        was_empty = not queue
        queue.extend(ambiguous)
        if was_empty:
            await _ask_next_pending(update.effective_chat.id, context)


async def _ask_next_pending(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the disambiguation prompt for the note at the head of the queue.

    Sends a fresh message (works whether triggered by an incoming note or after a
    previous pick was finalized). Leaves the head on the queue until it's answered.
    """
    queue = context.user_data.get(_PENDING_QUEUE, [])
    if not queue:
        return
    classified, candidates = queue[0]
    quote = classified.raw_quote
    if candidates:
        await context.bot.send_message(
            chat_id,
            f"🤔 ¿A quién te refieres? \"{quote}\"",
            reply_markup=_candidate_keyboard(candidates),
        )
    else:
        await context.bot.send_message(
            chat_id,
            f"🤔 No pude identificar de qué jugador habla \"{quote}\". "
            "Responde con un número (p. ej. `#8`) o un nombre.",
            parse_mode="Markdown",
        )


# ── callbacks (inline buttons) ───────────────────────────────────────────
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    svc = _svc(context)
    chat_id = update.effective_chat.id
    session = await svc.storage.get_active_session(chat_id)
    if session is None:
        await query.edit_message_text("La sesión ya no está activa.")
        return

    if data == "confirm_roster":
        await svc.confirm_roster(session)
        await query.edit_message_text(
            "✅ Alineación confirmada. Ya puedes enviar tus observaciones (voz o texto)."
        )
        return

    if data.startswith("pick:"):
        await _resolve_pick(query, context, session, data.split(":", 1)[1])


async def _resolve_pick(query, context, session: Session, picked: str) -> None:
    queue: list = context.user_data.get(_PENDING_QUEUE, [])
    if not queue:
        await query.edit_message_text("No hay nada pendiente.")
        return
    classified, candidates = queue[0]

    if picked == "skip":
        await query.edit_message_text("🚫 Omitida — nota descartada.")
    else:
        player = next((p for p in candidates if str(p.id) == picked), None)
        if player is None:
            await query.edit_message_text("No se encontró ese jugador.")
            return
        await _svc(context).resolve_disambiguation(session, classified, player)
        await query.edit_message_text(
            f"✅ Registrada para #{player.number} {player.name}."
        )

    # Done with the head; advance the queue (a fresh prompt for the next, if any).
    queue.pop(0)
    if queue:
        await _ask_next_pending(query.message.chat_id, context)
    else:
        context.user_data.pop(_PENDING_QUEUE, None)


# ── roster correction parsing ─────────────────────────────────────────────
async def _apply_roster_correction(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Session, text: str
) -> None:
    """Lightweight free-text roster fixes before confirmation.

    Supported (ES + EN): '#10 es Pérez' / '#10 is Pérez' (rename),
    'visitante #14 Gómez CB' / 'away #14 Gómez CB' (add/replace).
    Anything unparseable gets a gentle nudge.
    """
    import re

    svc = _svc(context)
    # Rename: accepts "es" (ES) or "is" (EN) as the linking verb.
    m = re.match(r"#?(\d{1,2})\s+(?:es|is)\s+(.+)", text, re.IGNORECASE)
    if m:
        number, new_name = int(m.group(1)), m.group(2).strip()
        for p in session.players:
            if p.number == number:
                p.name = new_name
        await svc.storage.replace_roster(session.id, list(session.players))
        await update.message.reply_text(
            f"✏️ Actualizado #{number} → {new_name}.",
            reply_markup=_confirm_keyboard(),
        )
        return

    # Add/replace: accepts local/visitante (ES) or home/away (EN) as the side.
    m = re.match(
        r"(local|visitante|home|away)\s+#?(\d{1,2})\s+(\S+)\s*(\S+)?",
        text,
        re.IGNORECASE,
    )
    if m:
        side = _parse_side(m.group(1))
        number, name = int(m.group(2)), m.group(3)
        position = m.group(4)
        await svc.add_missing_player(session, side, number, name, position)
        await update.message.reply_text(
            f"➕ Añadido {_side_label(side)} #{number} {name}.",
            reply_markup=_confirm_keyboard(),
        )
        return

    await update.message.reply_text(
        "No entendí esa corrección. Ejemplos:\n"
        "`#10 es Pérez`  o  `visitante #14 Gómez CB`\n"
        "O pulsa *Confirmar* si la alineación es correcta.",
        parse_mode="Markdown",
        reply_markup=_confirm_keyboard(),
    )


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


def _side_label(side: str) -> str:
    """Spanish label for a side, for user-facing messages."""
    return "local" if side == HOME else "visitante"


def _parse_match_arg(arg: str) -> tuple[str, str, str | None]:
    label = None
    if "|" in arg:
        arg, label = arg.split("|", 1)
        label = label.strip() or None
    parts = arg.split(" vs ") if " vs " in arg else arg.split(" - ")
    if len(parts) != 2:
        return "", "", None
    return parts[0].strip(), parts[1].strip(), label


def _parse_manual_lineup(raw: str) -> list[tuple[str, int | None, str, str | None]]:
    """Parse a free-text manual lineup into (side, number, name, position) tuples.

        'local: 10 Messi DC, 7 Di María; visitante: 5 Ramos'
        → [('home', 10, 'Messi', 'DC'), ('home', 7, 'Di María', None),
           ('away', 5, 'Ramos', None)]

    Pure (no Telegram/DB), so it's unit-testable on its own. Rules:
      - ';' separates side-segments; ',' separates players within a segment.
      - A 'side:' prefix (local/visitante/home/away) switches the current side;
        entries before any prefix default to home.
      - Per entry: an optional leading '#?number', then the name, then an
        optional trailing 2–3 letter UPPERCASE position token.
    """
    import re

    entries: list[tuple[str, int | None, str, str | None]] = []
    side = HOME
    for segment in raw.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        m = re.match(r"(local|visitante|home|away)\s*:\s*(.*)$", segment, re.IGNORECASE)
        if m:
            side = _parse_side(m.group(1))
            body = m.group(2)
        else:
            body = segment
        for chunk in body.split(","):
            parsed = _parse_player_chunk(chunk)
            if parsed is not None:
                number, name, position = parsed
                entries.append((side, number, name, position))
    return entries


def _parse_player_chunk(chunk: str) -> tuple[int | None, str, str | None] | None:
    """Parse one '10 Messi DC' style entry → (number, name, position) or None."""
    import re

    text = chunk.strip()
    if not text:
        return None
    number = None
    m = re.match(r"#?(\d{1,2})\s+(.*)$", text)
    if m:
        number = int(m.group(1))
        text = m.group(2).strip()
    position = None
    # A trailing 2–3 letter UPPERCASE token is the position (DC, CB, GK…).
    m = re.match(r"(.*\S)\s+([A-ZÁÉÍÓÚÑ]{2,3})$", text)
    if m and m.group(1).strip():
        text = m.group(1).strip()
        position = m.group(2)
    name = text.strip()
    if not name:
        return None
    return number, name, position


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
    app.add_handler(CommandHandler(["fin", "endmatch"], cmd_endmatch))
    app.add_handler(CommandHandler(["jugadores", "lineup"], cmd_manual_lineup))
    app.add_handler(CommandHandler("addplayer", cmd_addplayer))
    app.add_handler(CommandHandler("target", cmd_target))
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
