from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Score sheets filled in on the dashboard: one row per (match, player), the
    # criterion scores as a JSON document. A new table only — nothing captured
    # so far is rewritten.
    return """
        CREATE TABLE IF NOT EXISTS "evaluations" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "profile" VARCHAR(32),
    "scores" JSONB NOT NULL,
    "build_ok" BOOL,
    "height_ok" BOOL,
    "rating" DOUBLE PRECISION,
    "note" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "observation_id" INT REFERENCES "observations" ("id") ON DELETE SET NULL,
    "prospect_id" INT NOT NULL REFERENCES "prospects" ("id") ON DELETE CASCADE,
    "session_id" INT NOT NULL REFERENCES "sessions" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_evaluations_session_5b2e90" UNIQUE ("session_id", "prospect_id")
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "evaluations";"""
