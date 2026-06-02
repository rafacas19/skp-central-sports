"""Tortoise ORM initialization and configuration.

`TORTOISE_ORM` is the single config dict, consumed by both the running app
(via `init_db`) and Aerich (the migration tool) — Aerich's `tool.aerich` in
pyproject points at `scouting_bot.db.TORTOISE_ORM`.

The app connects to `DATABASE_URL`. Tortoise expects an asyncpg URL of the form
`postgres://user:pass@host:port/dbname`; Render/psycopg-style `postgresql://`
URLs are normalized here.
"""

from __future__ import annotations

from tortoise import Tortoise

from .config import settings


def _normalize_db_url(url: str) -> str:
    """Tortoise's asyncpg backend wants the `postgres://` scheme.

    Render and many tools emit `postgresql://`; rewrite it. Leave other schemes
    (e.g. sqlite://) untouched so tests can use sqlite.
    """
    if url.startswith("postgresql://"):
        return "postgres://" + url[len("postgresql://") :]
    return url


TORTOISE_ORM = {
    "connections": {"default": _normalize_db_url(settings.database_url)},
    "apps": {
        "models": {
            "models": ["scouting_bot.models", "aerich.models"],
            "default_connection": "default",
        }
    },
}


async def init_db(generate_schemas: bool = False) -> None:
    """Open the Tortoise connection pool.

    `generate_schemas=True` auto-creates tables (used in tests and as a
    convenience). In production, Aerich migrations own the schema, so this is
    called with generate_schemas=False and `aerich upgrade` runs at deploy.
    """
    await Tortoise.init(config=TORTOISE_ORM)
    if generate_schemas:
        await Tortoise.generate_schemas(safe=True)


async def close_db() -> None:
    await Tortoise.close_connections()
