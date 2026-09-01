from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Team categories derived from the team name ("Santa Fe U18" → "Sub-18"):
    # one column on prospects (the player's club) and one per side on sessions.
    # `sessions.category` is NOT touched — that one is typed by the scout on
    # /nuevo and keeps its meaning. All nullable, additive and IF NOT EXISTS, so
    # this is safe to re-run and composes with generate_schemas(safe=True).
    # Existing rows read NULL until the backfill script is run (scripts/
    # backfill_categories.py); no existing value is rewritten by this migration.
    return """
        ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "category" TEXT;
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "home_team_category" TEXT;
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "away_team_category" TEXT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sessions" DROP COLUMN IF EXISTS "away_team_category";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "home_team_category";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "category";"""
