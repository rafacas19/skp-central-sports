"""Configuration loaded from environment (.env).

All runtime knobs live here so the rest of the codebase never reads os.environ
directly. Import the module-level `settings` singleton.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    use_mock_ai: bool = _bool("USE_MOCK_AI", True)

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_transcribe_model: str = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")

    database_path: str = os.getenv("DATABASE_PATH", "scouting.db")

    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.65"))
    session_nudge_minutes: int = int(os.getenv("SESSION_NUDGE_MINUTES", "120"))


settings = Settings()
