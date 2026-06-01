"""Run the bot:  python -m scouting_bot"""

from __future__ import annotations

import logging

from .bot import build_application
from .config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    mode = "MOCK AI" if settings.use_mock_ai else "LIVE AI (Claude + Whisper)"
    logging.getLogger(__name__).info("Starting scouting bot — %s", mode)
    app = build_application()
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
