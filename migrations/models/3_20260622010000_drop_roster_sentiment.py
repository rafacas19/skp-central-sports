from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Cleanup migration: the observation-first pivot removed the roster Player
    # table and the auto-classification columns. Their readers are all gone, so
    # we drop them. CASCADE clears the now-orphan observations.player_id FK.
    return """
        ALTER TABLE "observations" DROP COLUMN IF EXISTS "player_id";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "sentiment";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "skill_category";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "roster_confirmed";
DROP TABLE IF EXISTS "players" CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    # Best-effort recreation (the roster feature is gone; this is for symmetry).
    return """
        CREATE TABLE IF NOT EXISTS "players" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "side" VARCHAR(8) NOT NULL,
    "number" INT,
    "name" TEXT NOT NULL,
    "position" VARCHAR(80),
    "is_target" BOOL NOT NULL DEFAULT False,
    "session_id" INT NOT NULL REFERENCES "sessions" ("id") ON DELETE CASCADE
);
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "roster_confirmed" BOOL NOT NULL DEFAULT False;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "skill_category" VARCHAR(32);
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "sentiment" VARCHAR(16);
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "player_id" INT REFERENCES "players" ("id") ON DELETE SET NULL;"""
