from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Contact follow-up on a prospect: where the conversation stands, when it
    # last happened, and what came out of it. All nullable — a NULL status means
    # "Sin contactar", so no existing row needs a value and nothing is rewritten.
    # Additive and IF NOT EXISTS, safe to re-run, composes with
    # generate_schemas(safe=True).
    return """
        ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "contact_status" VARCHAR(32);
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "last_contact_at" DATE;
ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "contact_notes" TEXT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "prospects" DROP COLUMN IF EXISTS "contact_notes";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "last_contact_at";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "contact_status";"""
