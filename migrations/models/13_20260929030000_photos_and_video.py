from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Photos uploaded from the dashboard (their own table, so list queries never
    # carry image bytes) and a highlights link on the player. Nothing existing
    # is rewritten: the column starts NULL and the table empty.
    return """
        ALTER TABLE "prospects" ADD COLUMN IF NOT EXISTS "video_url" TEXT;
CREATE TABLE IF NOT EXISTS "prospect_photos" (
    "prospect_id" INT NOT NULL PRIMARY KEY REFERENCES "prospects" ("id") ON DELETE CASCADE,
    "data" BYTEA NOT NULL,
    "mime" VARCHAR(32) NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "prospect_photos";
ALTER TABLE "prospects" DROP COLUMN IF EXISTS "video_url";"""
