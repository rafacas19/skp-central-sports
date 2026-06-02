from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Widen players.position from VARCHAR(16) to VARCHAR(80). Hand-written because
    # Aerich's auto-differ does not reliably detect max_length-only changes.
    # Widening a varchar is non-destructive: all existing values still fit.
    return """
        ALTER TABLE "players" ALTER COLUMN "position" TYPE VARCHAR(80);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    # Reverting could truncate values longer than 16 chars; USING ::VARCHAR(16)
    # makes the intent explicit. Only safe if no value exceeds 16 chars.
    return """
        ALTER TABLE "players" ALTER COLUMN "position" TYPE VARCHAR(16) USING "position"::VARCHAR(16);"""
