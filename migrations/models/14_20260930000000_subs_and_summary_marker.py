from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Substitutions made on the dashboard pitch, and a fingerprint of the score
    # sheets behind a player's AI summary. Additive: new nullable columns and a
    # boolean defaulting to false; no row is rewritten.
    return """
        ALTER TABLE "match_players" ADD COLUMN IF NOT EXISTS "came_on_for_id" INT;
ALTER TABLE "match_players" ADD COLUMN IF NOT EXISTS "subbed_off" BOOL NOT NULL DEFAULT False;
ALTER TABLE "match_players" ADD COLUMN IF NOT EXISTS "sub_minute" INT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "ai_summary_marker" TEXT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "prospects" DROP COLUMN IF EXISTS "ai_summary_marker";
ALTER TABLE "match_players" DROP COLUMN IF EXISTS "sub_minute";
ALTER TABLE "match_players" DROP COLUMN IF EXISTS "subbed_off";
ALTER TABLE "match_players" DROP COLUMN IF EXISTS "came_on_for_id";"""
