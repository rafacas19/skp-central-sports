from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Observation-first pivot, Phase 1 (additive only): new cross-match identity
    # (prospects), per-chat scout names, and the new observation/session columns.
    # Nothing is dropped here — the old roster Player table and the
    # sentiment/skill/roster_confirmed columns are removed in a later migration
    # once their readers are gone. All statements are IF NOT EXISTS so this is
    # safe to re-run and composes with generate_schemas(safe=True).
    return """
        CREATE TABLE IF NOT EXISTS "scout_profiles" (
    "agent_chat_id" BIGINT NOT NULL PRIMARY KEY,
    "name" TEXT NOT NULL
);
COMMENT ON TABLE "scout_profiles" IS 'Per-chat scout identity (one scout per chat).';
CREATE TABLE IF NOT EXISTS "prospects" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "agent_chat_id" BIGINT NOT NULL,
    "name" TEXT NOT NULL,
    "normalized_name" TEXT NOT NULL,
    "team" TEXT,
    "normalized_team" TEXT,
    "position" VARCHAR(80),
    "age" INT,
    "height_cm" INT,
    "latest_rating" DOUBLE PRECISION,
    "decision_status" VARCHAR(24),
    "is_temporary" BOOL NOT NULL  DEFAULT False,
    "photo_file_id" TEXT,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS "idx_prospects_chat_name_team" ON "prospects" ("agent_chat_id", "normalized_name", "normalized_team");
COMMENT ON TABLE "prospects" IS 'A scouted player with a cross-match identity, owned by one scout (chat).';
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "scout_name" TEXT;
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "competition" TEXT;
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "category" TEXT;
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "location" TEXT;
ALTER TABLE "sessions" ADD COLUMN IF NOT EXISTS "match_date" TIMESTAMPTZ;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "prospect_id" INT REFERENCES "prospects" ("id") ON DELETE SET NULL;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "team" TEXT;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "player_name" TEXT;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "player_number" INT;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "player_position" VARCHAR(80);
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "source" VARCHAR(8);
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "rating" DOUBLE PRECISION;
ALTER TABLE "observations" ADD COLUMN IF NOT EXISTS "is_team_note" BOOL NOT NULL DEFAULT False;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "observations" DROP COLUMN IF EXISTS "is_team_note";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "rating";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "source";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "player_position";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "player_number";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "player_name";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "team";
ALTER TABLE "observations" DROP COLUMN IF EXISTS "prospect_id";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "match_date";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "location";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "category";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "competition";
ALTER TABLE "sessions" DROP COLUMN IF EXISTS "scout_name";
DROP TABLE IF EXISTS "prospects";
DROP TABLE IF EXISTS "scout_profiles";"""
