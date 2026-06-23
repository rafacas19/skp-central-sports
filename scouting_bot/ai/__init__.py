"""AI provider layer.

A single swappable interface (`AIProvider`) covers the intelligence tasks:
transcription, note classification (identity extraction), and the cross-match
player summary. Concrete implementations: `MockAIProvider` (no keys,
deterministic) and `RealAIProvider` (Claude + Whisper). `get_provider()` picks
based on config.
"""

from .base import AIProvider, ClassifiedNote, PlayerMatch
from .mock import MockAIProvider


def get_provider() -> AIProvider:
    """Return the configured provider (mock unless USE_MOCK_AI=false)."""
    from ..config import settings

    if settings.use_mock_ai:
        return MockAIProvider()

    from .real import RealAIProvider

    return RealAIProvider()


__all__ = [
    "AIProvider",
    "ClassifiedNote",
    "PlayerMatch",
    "MockAIProvider",
    "get_provider",
]
