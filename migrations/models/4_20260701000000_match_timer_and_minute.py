from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # "Intelligent assistant" feedback (Jun 2026), additive: a match clock on the
    # session (each half's wall-clock start), plus per-observation match minute and
    # a substitution flag. All statements are IF NOT EXISTS so this is safe to
    # re-run and composes with generate_schemas(safe=True).
    return """
        ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "first_half_started_at" TIMESTAMPTZ;
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "second_half_started_at" TIMESTAMPTZ;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "minute" INT;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "is_substitution" BOOL NOT NULL DEFAULT False;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "observations" DROP COLUMN IF EXISTS "is_substitution";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "minute";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "second_half_started_at";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "first_half_started_at";"""
