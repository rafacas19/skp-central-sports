from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Matches created on the dashboard, and their lineups. Every existing match
    # was created by the bot, so `origin` defaults to 'bot' and nothing else
    # changes for them; the formations start empty.
    return """
        ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "origin" VARCHAR(8) NOT NULL DEFAULT 'bot';
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "home_formation" VARCHAR(8);
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "away_formation" VARCHAR(8);
CREATE TABLE IF NOT EXISTS "match_players" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "side" VARCHAR(8) NOT NULL,
    "slot" INT,
    "shirt_number" INT,
    "prospect_id" INT NOT NULL REFERENCES "prospects" ("id") ON DELETE CASCADE,
    "session_id" INT NOT NULL REFERENCES "sessions" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_match_playe_session_8d41c7" UNIQUE ("session_id", "prospect_id")
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "match_players";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "away_formation";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "home_formation";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "origin";"""
