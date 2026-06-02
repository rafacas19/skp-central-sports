"""Configuration loaded from environment (.env).

All runtime knobs live here so the rest of the codebase never reads os.environ
directly. Import the module-level `settings` singleton.

Values are validated at load time (`Settings.from_env`) so a malformed env var
fails fast at startup with a clear message, rather than crashing deep in a
request or silently using a wrong value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when an environment value is missing or malformed."""


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _https_url(*names: str) -> str:
    """Read the first set base URL from `names`, tolerating a bare hostname.

    Falls back across names in order — used so an explicit WEBHOOK_BASE_URL wins,
    but Render's auto-injected RENDER_EXTERNAL_URL is used when it isn't set. A
    bare hostname (no scheme) gets https:// prepended. All empty → "" (polling).
    """
    for name in names:
        raw = os.getenv(name, "").strip().rstrip("/")
        if not raw:
            continue
        if raw.startswith(("http://", "https://")):
            return raw
        return f"https://{raw}"
    return ""


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str

    use_mock_ai: bool

    anthropic_api_key: str
    anthropic_model: str

    openai_api_key: str
    openai_transcribe_model: str

    database_url: str

    confidence_threshold: float
    session_nudge_minutes: int

    # ── Webhook / web-service runtime (production on Render) ─────────────
    # When webhook_base_url is set the bot runs in webhook mode (a web service
    # bound to $PORT). When it is empty the bot falls back to long-polling,
    # which is the convenient mode for local development.
    port: int
    webhook_base_url: str
    webhook_secret: str

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            use_mock_ai=_bool("USE_MOCK_AI", True),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_transcribe_model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1"),
            database_url=os.getenv("DATABASE_URL", ""),
            confidence_threshold=_float("CONFIDENCE_THRESHOLD", 0.65),
            session_nudge_minutes=_int("SESSION_NUDGE_MINUTES", 120),
            port=_int("PORT", 10000),
            # Explicit override wins; otherwise use Render's auto-injected URL.
            webhook_base_url=_https_url("WEBHOOK_BASE_URL", "RENDER_EXTERNAL_URL"),
            webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
        )
        s.validate()
        return s

    def validate(self) -> None:
        """Fail fast on impossible configurations.

        Only checks invariants that are always required; mode-specific checks
        (e.g. AI keys) are surfaced by `require_ai_keys()` at the point of use so
        the mock path stays runnable with no keys at all.
        """
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ConfigError(
                f"CONFIDENCE_THRESHOLD must be between 0 and 1, "
                f"got {self.confidence_threshold}"
            )
        if self.session_nudge_minutes <= 0:
            raise ConfigError(
                f"SESSION_NUDGE_MINUTES must be positive, got {self.session_nudge_minutes}"
            )
        if self.webhook_base_url and not self.webhook_base_url.startswith("https://"):
            # Telegram only delivers webhooks over HTTPS.
            raise ConfigError(
                f"WEBHOOK_BASE_URL must start with https://, got {self.webhook_base_url!r}"
            )

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_base_url)

    def require_ai_keys(self) -> None:
        """Validate that live-AI mode has the keys it needs (hardening #6).

        Called at startup when USE_MOCK_AI=false so a misconfigured deploy fails
        immediately with a clear message instead of erroring on the first note.
        """
        if self.use_mock_ai:
            return
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if missing:
            raise ConfigError(
                "USE_MOCK_AI=false but missing required key(s): "
                + ", ".join(missing)
                + ". Set them or use USE_MOCK_AI=true."
            )


settings = Settings.from_env()
