from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Production carries a hand-made partial unique index allowing ONE active
    # session per scout. That is the bot's rule — a Telegram note goes to the
    # scout's open match — but it also blocked every match created on the
    # dashboard while another one was open. Narrow it to bot matches: a scout
    # still has at most one active bot match, and any number of web matches
    # (which the bot never picks up; see Storage.get_active_session).
    # IF EXISTS / IF NOT EXISTS: databases that never had the index (local,
    # tests) simply gain the narrowed one.
    return """
        DROP INDEX IF EXISTS "uq_sessions_one_active_per_agent";
CREATE UNIQUE INDEX IF NOT EXISTS "uq_sessions_one_active_bot_per_agent"
    ON "sessions" ("agent_chat_id") WHERE "state" = 'active' AND "origin" = 'bot';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uq_sessions_one_active_bot_per_agent";
CREATE UNIQUE INDEX IF NOT EXISTS "uq_sessions_one_active_per_agent"
    ON "sessions" ("agent_chat_id") WHERE "state" = 'active';"""
