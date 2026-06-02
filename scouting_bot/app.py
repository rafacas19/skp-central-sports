"""FastAPI application — the single web process.

Responsibilities:
  • Lifespan: open Tortoise, build + start the python-telegram-bot Application,
    register the Telegram webhook (in webhook mode). Tear all of it down on exit.
  • POST /telegram/{secret}: receive Telegram updates and feed them to PTB.
  • GET  /healthz: liveness/readiness probe (open, used by Render's health check).
  • Read API (X-API-Key): sessions / players / observations / report — for a
    future dashboard. The bot still does all dialog logic via PTB.

PTB runs *inside* this process (no webhook server of its own); we call
`application.process_update()` per request, so there is one event loop.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from telegram import Update

from .bot import build_application
from .config import settings
from .db import close_db, init_db
from .report import build_markdown, build_summary
from .storage import Storage

logger = logging.getLogger(__name__)

_ALLOWED_UPDATES = ["message", "callback_query"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Database. In production Aerich owns the schema, but generate_schemas is
    #    idempotent (safe=True) and keeps local/first-boot simple.
    await init_db(generate_schemas=True)

    # 2. Telegram bot. Build handlers, then initialize+start the Application so
    #    its JobQueue (auto-nudge) runs in this loop.
    #
    #    Initialization talks to Telegram (validates the token). If that fails —
    #    bad/placeholder token, Telegram outage — we log it and still serve the
    #    REST API and health check rather than crashing the whole process. The
    #    bot is simply unavailable until the next deploy fixes the cause.
    application = None
    app.state.telegram_app = None
    try:
        application = build_application()
        await application.initialize()
        await application.start()
        app.state.telegram_app = application

        if settings.use_webhook:
            webhook_url = (
                f"{settings.webhook_base_url}/telegram/{settings.telegram_bot_token}"
            )
            await application.bot.set_webhook(
                url=webhook_url,
                secret_token=settings.webhook_secret or None,
                allowed_updates=_ALLOWED_UPDATES,
                drop_pending_updates=True,
            )
            logger.info("Webhook registered: %s", webhook_url)
        else:
            logger.warning(
                "WEBHOOK_BASE_URL not set — no webhook registered. The bot will "
                "not receive updates unless you set a webhook or poll separately."
            )
    except Exception:  # noqa: BLE001 — keep the web app alive even if the bot fails
        logger.exception(
            "Telegram bot failed to start — serving API/health only. "
            "Check TELEGRAM_BOT_TOKEN and Telegram connectivity."
        )
        if application is not None:
            try:
                await application.shutdown()
            except Exception:  # noqa: BLE001
                pass
            application = None
            app.state.telegram_app = None

    try:
        yield
    finally:
        # NOTE: deliberately do NOT delete_webhook() here. During a rolling
        # deploy the departing instance would clear the webhook that the new
        # instance just registered, leaving the bot silent until manually reset.
        # The webhook is owned by whichever instance last called set_webhook on
        # startup; leaving it in place is correct across restarts.
        if application is not None:
            await application.stop()
            await application.shutdown()
        await close_db()


app = FastAPI(
    title="Scouting Bot",
    description="Telegram match-scouting bot + read API.",
    version="2.0.0",
    lifespan=lifespan,
)


# ── Telegram webhook ─────────────────────────────────────────────────────
@app.post("/telegram/{secret}", include_in_schema=False)
async def telegram_webhook(secret: str, request: Request):
    """Receive a Telegram update and hand it to PTB.

    Two layers of authentication:
      • the URL path segment must equal the bot token (a shared secret), and
      • Telegram echoes WEBHOOK_SECRET in the X-Telegram-Bot-Api-Secret-Token
        header, which we verify.
    """
    if secret != settings.telegram_bot_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if settings.webhook_secret:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header != settings.webhook_secret:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    application = request.app.state.telegram_app
    if application is None:
        # Bot failed to initialize (see startup logs). Ack so Telegram doesn't
        # hammer retries, but do nothing.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram bot is not available.",
        )
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}


# ── Health check ───────────────────────────────────────────────────────────
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ── Read API (X-API-Key) ─────────────────────────────────────────────────
async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Guard for the read endpoints. Requires API_KEY to be configured."""
    if not settings.api_key:
        # No key configured ⇒ the read API is disabled rather than open.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Read API is not configured (set API_KEY).",
        )
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing X-API-Key."
        )


_storage = Storage()


def _session_dict(session, *, include_children: bool) -> dict:
    out = {
        "id": session.id,
        "agent_chat_id": session.agent_chat_id,
        "home_team": session.home_team,
        "away_team": session.away_team,
        "label": session.label,
        "state": session.state,
        "roster_confirmed": session.roster_confirmed,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "observation_count": len(session.observations),
    }
    if include_children:
        out["players"] = [
            {
                "id": p.id,
                "side": p.side,
                "number": p.number,
                "name": p.name,
                "position": p.position,
                "is_target": p.is_target,
            }
            for p in session.players
        ]
        out["observations"] = [
            {
                "id": o.id,
                "player_id": o.player_id,
                "side": o.side,
                "sentiment": o.sentiment,
                "skill_category": o.skill_category,
                "raw_quote": o.raw_quote,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in session.observations
        ]
    return out


@app.get("/sessions", dependencies=[Depends(require_api_key)])
async def list_sessions():
    """All sessions (summary only)."""
    from .models import Session

    sessions = await Session.all().prefetch_related("observations").order_by("-id")
    return {"sessions": [_session_dict(s, include_children=False) for s in sessions]}


@app.get("/sessions/{session_id}", dependencies=[Depends(require_api_key)])
async def get_session(session_id: int):
    """One session with full roster + observations."""
    try:
        session = await _storage.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    return _session_dict(session, include_children=True)


@app.get("/sessions/{session_id}/report", dependencies=[Depends(require_api_key)])
async def get_report(session_id: int):
    """Rendered report (Telegram summary + full markdown) for a session."""
    try:
        session = await _storage.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    return {
        "session_id": session.id,
        "summary": build_summary(session),
        "markdown": build_markdown(session),
    }
