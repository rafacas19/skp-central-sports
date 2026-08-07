from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Dashboard Phase 5: per-prospect cached AI summary plus the observation
    # count it was generated from (staleness watermark). Additive and
    # IF NOT EXISTS so it is safe to re-run and composes with
    # generate_schemas(safe=True).
    return """
        ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "ai_summary" TEXT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "ai_summary_obs_count" INT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "prospects" DROP COLUMN IF EXISTS "ai_summary_obs_count";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "ai_summary";"""
