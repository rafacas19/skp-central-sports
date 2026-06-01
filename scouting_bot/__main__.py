"""Run the bot:  python -m scouting_bot

Two run modes, chosen automatically by config:

  • Webhook mode (production / Render): when WEBHOOK_BASE_URL is set, the bot
    runs as a web service bound to $PORT, and Telegram pushes updates to a
    secret URL path. This is what Render's Web Service expects.

  • Polling mode (local dev): when WEBHOOK_BASE_URL is empty, the bot long-polls
    Telegram — zero infra, nothing to expose.
"""

from __future__ import annotations

import logging

from .bot import build_application
from .config import settings

logger = logging.getLogger(__name__)

_ALLOWED_UPDATES = ["message", "callback_query"]


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mode = "MOCK AI" if settings.use_mock_ai else "LIVE AI (Claude + Whisper)"
    app = build_application()

    if settings.use_webhook:
        # The URL path doubles as a shared secret so only Telegram can reach it.
        # python-telegram-bot also verifies the X-Telegram-Bot-Api-Secret-Token
        # header against `secret_token` when provided.
        url_path = settings.telegram_bot_token
        webhook_url = f"{settings.webhook_base_url}/{url_path}"
        logger.info(
            "Starting scouting bot — %s — WEBHOOK mode on :%s", mode, settings.port
        )
        app.run_webhook(
            listen="0.0.0.0",
            port=settings.port,
            url_path=url_path,
            webhook_url=webhook_url,
            secret_token=settings.webhook_secret or None,
            allowed_updates=_ALLOWED_UPDATES,
            drop_pending_updates=True,
        )
    else:
        logger.info("Starting scouting bot — %s — POLLING mode", mode)
        app.run_polling(allowed_updates=_ALLOWED_UPDATES)


if __name__ == "__main__":
    main()
