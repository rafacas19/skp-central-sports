from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Dashboard accounts with roles, replacing the shared password. A new table
    # only; the first admin is seeded at startup from ADMIN_USERNAME and
    # ADMIN_TEMP_PASSWORD (see dashboard/users.py), not by this migration.
    return """
        CREATE TABLE IF NOT EXISTS "dashboard_users" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "username" VARCHAR(40) NOT NULL UNIQUE,
    "display_name" VARCHAR(80) NOT NULL,
    "role" VARCHAR(8) NOT NULL DEFAULT 'read',
    "password_hash" TEXT NOT NULL,
    "must_change_password" BOOL NOT NULL DEFAULT True,
    "is_active" BOOL NOT NULL DEFAULT True,
    "session_version" INT NOT NULL DEFAULT 1,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_login_at" TIMESTAMPTZ
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "dashboard_users";"""
