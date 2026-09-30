from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # The written part of the profile report (AI draft, editable). New table only.
    return """
        CREATE TABLE IF NOT EXISTS "profile_reports" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "profile" VARCHAR(32) NOT NULL,
    "summary" TEXT,
    "sections" JSONB NOT NULL,
    "edited" BOOL NOT NULL DEFAULT False,
    "watermark" TEXT,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "prospect_id" INT NOT NULL REFERENCES "prospects" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_profile_rep_prospec_2a9f63" UNIQUE ("prospect_id", "profile")
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "profile_reports";"""
