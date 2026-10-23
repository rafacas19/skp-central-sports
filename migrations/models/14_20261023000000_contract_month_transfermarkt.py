from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # "Contrato hasta" gains an optional month next to the existing year, and a
    # profile can link the player's Transfermarkt page. Both nullable: existing
    # contracts keep their year and simply have no month, so nothing is
    # rewritten. Height is untouched — it stays in centimetres and only its
    # input and display move to metres. Additive and IF NOT EXISTS, safe to
    # re-run, composes with generate_schemas(safe=True).
    return """
        ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "contract_month" INT;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "transfermarkt_url" TEXT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "prospects" DROP COLUMN IF EXISTS "transfermarkt_url";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "contract_month";"""
