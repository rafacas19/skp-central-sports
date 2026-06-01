"""Test fixtures — Postgres-backed (the project is Postgres-only).

A test database is required. Point TEST_DATABASE_URL (or DATABASE_URL) at a
throwaway Postgres. If none is reachable the suite is skipped with a clear
message rather than erroring confusingly.

  # quick local Postgres for tests:
  docker run -d --name pg-test -e POSTGRES_PASSWORD=test \\
      -e POSTGRES_DB=scouting_test -p 5432:5432 postgres:16-alpine
  export TEST_DATABASE_URL=postgresql://postgres:test@localhost:5432/scouting_test

Each test runs against a clean schema (all tables dropped & recreated), so tests
are isolated and order-independent.
"""

import os

import pytest

from scouting_bot.ai.mock import MockAIProvider
from scouting_bot.service import ScoutingService
from scouting_bot.storage import Storage

_DSN = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _reachable(dsn: str) -> bool:
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


# Resolved once at collection time so the skip reason is clear and uniform.
_AVAILABLE = bool(_DSN) and _reachable(_DSN)
_SKIP = pytest.mark.skipif(
    not _AVAILABLE,
    reason=(
        "No test Postgres reachable. Set TEST_DATABASE_URL (or DATABASE_URL) to a "
        "throwaway Postgres — see tests/conftest.py."
    ),
)

# Apply the skip to every test in the suite (the whole project needs Postgres).
pytestmark = _SKIP


def _truncate(storage: Storage) -> None:
    """Wipe all rows + reset identities so each test starts from a clean slate."""
    with storage._pool.connection() as conn:  # noqa: SLF001 — test-only helper
        conn.execute(
            "TRUNCATE observations, players, sessions RESTART IDENTITY CASCADE"
        )


@pytest.fixture
def storage():
    if not _AVAILABLE:
        pytest.skip("No test Postgres reachable.")
    s = Storage(_DSN)
    _truncate(s)  # clean before, so a prior crashed run can't leak state
    yield s
    s.close()


@pytest.fixture
def service(storage):
    return ScoutingService(storage, MockAIProvider(), confidence_threshold=0.65)
