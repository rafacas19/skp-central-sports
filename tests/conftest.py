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
