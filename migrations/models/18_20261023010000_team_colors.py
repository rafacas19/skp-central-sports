from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # The shirt colour each side wears on the dashboard pitch ("#c8102e"),
    # picked on the formation step. Nullable: a match without one keeps the
    # pitch's original kit, so no existing row is rewritten. Additive and IF
    # NOT EXISTS, safe to re-run, composes with generate_schemas(safe=True).
    return """
        ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "home_color" VARCHAR(7);
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "away_color" VARCHAR(7);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sessions" DROP COLUMN IF EXISTS "away_color";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "home_color";"""
