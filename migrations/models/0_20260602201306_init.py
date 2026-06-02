from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "sessions" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "agent_chat_id" BIGINT NOT NULL,
    "home_team" TEXT NOT NULL,
    "away_team" TEXT NOT NULL,
    "label" TEXT,
    "state" VARCHAR(16) NOT NULL  DEFAULT 'active',
    "roster_confirmed" BOOL NOT NULL  DEFAULT False,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "last_activity_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "ended_at" TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS "idx_sessions_agent_c_dec145" ON "sessions" ("agent_chat_id", "state");
COMMENT ON TABLE "sessions" IS 'One match-scouting episode owned by one agent.';
CREATE TABLE IF NOT EXISTS "players" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "side" VARCHAR(8) NOT NULL,
    "number" INT,
    "name" TEXT NOT NULL,
    "position" VARCHAR(16),
    "is_target" BOOL NOT NULL  DEFAULT False,
    "session_id" INT NOT NULL REFERENCES "sessions" ("id") ON DELETE CASCADE
);
COMMENT ON TABLE "players" IS 'A roster entry within a single match (no cross-match identity in MVP).';
CREATE TABLE IF NOT EXISTS "observations" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "side" VARCHAR(8),
    "sentiment" VARCHAR(16),
    "skill_category" VARCHAR(32),
    "raw_quote" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "player_id" INT REFERENCES "players" ("id") ON DELETE SET NULL,
    "session_id" INT NOT NULL REFERENCES "sessions" ("id") ON DELETE CASCADE
);
COMMENT ON TABLE "observations" IS 'The atomic scouting note.';
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """
