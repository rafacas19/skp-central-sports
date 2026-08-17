from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Dashboard Phase 6: the scouting bio fields the dashboard lets the client
    # edit (preferred foot, dorsal, birth year, nationality, weight, origin club,
    # agent contact, estimated market value in USD, contract year). All nullable,
    # additive and IF NOT EXISTS, so this is safe to re-run and composes with
    # generate_schemas(safe=True). No existing column or value is touched.
    return """
        ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "birth_year" INT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "preferred_foot" VARCHAR(16);
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "shirt_number" INT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "nationality" TEXT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "weight_kg" INT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "origin_club" TEXT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "agent_name" TEXT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "agent_phone" TEXT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "market_value_usd" INT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "contract_year" INT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "prospects" DROP COLUMN IF EXISTS "contract_year";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "market_value_usd";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "agent_phone";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "agent_name";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "origin_club";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "weight_kg";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "nationality";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "shirt_number";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "preferred_foot";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "birth_year";"""
