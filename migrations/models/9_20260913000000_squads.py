from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # National-team squads ("selecciones") and their membership rows. Two new
    # tables, nothing rewritten: no existing column changes and no row is
    # touched, so a deploy that runs this is a no-op for everything already
    # captured. IF NOT EXISTS keeps it re-runnable and composes with
    # generate_schemas(safe=True) in tests.
    return """
        CREATE TABLE IF NOT EXISTS "squads" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "agent_chat_id" BIGINT NOT NULL,
    "name" TEXT NOT NULL,
    "normalized_name" TEXT NOT NULL,
    "category" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS "idx_squads_agent_c_9f4b1a" ON "squads" ("agent_chat_id", "normalized_name");
CREATE TABLE IF NOT EXISTS "squad_members" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "added_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "prospect_id" INT NOT NULL REFERENCES "prospects" ("id") ON DELETE CASCADE,
    "squad_id" INT NOT NULL REFERENCES "squads" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_squad_membe_squad_i_3c7d21" UNIQUE ("squad_id", "prospect_id")
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "squad_members";
DROP TABLE IF EXISTS "squads";"""
