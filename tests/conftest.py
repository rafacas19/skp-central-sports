"""Test fixtures — async, Postgres-backed via Tortoise.

Tests run against the same engine as production (Postgres). Provide a database
through DATABASE_URL (or TEST_DATABASE_URL); docker-compose's `db` service is the
intended local source:

    docker compose run --rm api pytest

If no database is reachable the suite is skipped with a clear message rather than
erroring confusingly. Each test starts from a clean schema (tables truncated).
"""

import os

import pytest
import pytest_asyncio
from tortoise import Tortoise

from scouting_bot.ai.mock import MockAIProvider
from scouting_bot.db import _normalize_db_url
from scouting_bot.service import ScoutingService
from scouting_bot.storage import Storage

_DSN = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or ""

_TORTOISE_TEST_CONFIG = {
    "connections": {"default": _normalize_db_url(_DSN)} if _DSN else {},
    "apps": {
        "models": {
            "models": ["scouting_bot.models"],
            "default_connection": "default",
        }
    },
}

_SKIP = pytest.mark.skipif(
    not _DSN,
    reason=(
        "No test database. Set TEST_DATABASE_URL or DATABASE_URL to a Postgres "
        "(docker compose run --rm api pytest)."
    ),
)
pytestmark = _SKIP


@pytest_asyncio.fixture
async def storage():
    if not _DSN:
        pytest.skip("No test database configured.")
    await Tortoise.init(config=_TORTOISE_TEST_CONFIG)
    await Tortoise.generate_schemas(safe=True)
    # Clean slate so tests are isolated and order-independent.
    conn = Tortoise.get_connection("default")
    await conn.execute_query(
        "TRUNCATE observations, players, sessions RESTART IDENTITY CASCADE"
    )
    try:
        yield Storage()
    finally:
        await Tortoise.close_connections()


@pytest_asyncio.fixture
async def service(storage):
    return ScoutingService(storage, MockAIProvider(), confidence_threshold=0.65)


@pytest_asyncio.fixture
async def harness(storage, request):
    """Webhook-level E2E harness: real FastAPI webhook → PTB → handlers → DB,
    with only the Telegram network faked. Reuses the `storage` fixture's Tortoise
    init/truncate (so we stay on one event loop and one connection pool).

    Under `pytest -s` (capture disabled) it prints the conversation as a
    transcript so you can eyeball a flow; a plain `pytest` run stays silent."""
    import httpx
    from telegram.ext import Application

    from scouting_bot.app import app as fastapi_app
    from scouting_bot.bot import register_handlers
    from scouting_bot.config import settings

    from .e2e_harness import FakeBot, Harness, Outbox

    outbox = Outbox()
    fake_bot = FakeBot(outbox)
    # The mock AI ignores image/audio bytes, but stage something for get_file().
    fake_bot.stage_file("ph_l", b"\xff\xd8fakejpeg")
    fake_bot.stage_file("vo_1", b"OggSfakeaudio")

    service = ScoutingService(storage, MockAIProvider(), confidence_threshold=0.65)

    # Build a real PTB Application bound to the fake bot (no token, no updater,
    # no network), with the SAME handler set as production via register_handlers.
    application = Application.builder().bot(fake_bot).updater(None).build()
    application.bot_data["service"] = service
    register_handlers(application)
    await application.initialize()  # no-op bot → no network; flips _initialized

    # The webhook path checks `secret == settings.telegram_bot_token`. Make sure
    # it's non-empty and known (settings is a frozen dataclass → object.__setattr__).
    token = settings.telegram_bot_token or "test-token"
    if not settings.telegram_bot_token:
        object.__setattr__(settings, "telegram_bot_token", token)

    # Drive the REAL endpoint, but bypass the lifespan: ASGITransport doesn't run
    # startup/shutdown, so we set telegram_app ourselves and the real init/webhook
    # code never fires.
    fastapi_app.state.telegram_app = application
    transport = httpx.ASGITransport(app=fastapi_app)
    # Auto-enable the transcript when output capture is off (pytest -s).
    transcript = request.config.getoption("capture") == "no"
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield Harness(client, outbox, fake_bot, token, transcript=transcript)
    finally:
        fastapi_app.state.telegram_app = None
        await application.shutdown()
