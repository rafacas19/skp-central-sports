"""Run the web app:  python -m scouting_bot

Starts Uvicorn serving the FastAPI app (scouting_bot.app:app), which owns the
Telegram bot (PTB Application) in its lifespan. In production the Docker image's
CMD runs uvicorn directly; this entrypoint is the convenient local equivalent.
"""

from __future__ import annotations

import logging

import uvicorn

from .config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mode = "MOCK AI" if settings.use_mock_ai else "LIVE AI (Claude + Whisper)"
    transport = "WEBHOOK" if settings.use_webhook else "NO WEBHOOK (set WEBHOOK_BASE_URL)"
    logging.getLogger(__name__).info(
        "Starting scouting web app — %s — %s — :%s", mode, transport, settings.port
    )
    uvicorn.run(
        "scouting_bot.app:app",
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
