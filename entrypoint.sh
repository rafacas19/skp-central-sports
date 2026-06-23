#!/usr/bin/env sh
set -e

# Apply database migrations. A failure here is FATAL: `set -e` aborts the
# script and the container exits non-zero, failing the deploy — rather than
# booting on a half-migrated schema (e.g. new tables created by the app's
# generate_schemas() while an ALTER/DROP migration silently didn't run).
if [ -d "migrations" ]; then
  echo "[entrypoint] running aerich upgrade..."
  aerich upgrade
fi

echo "[entrypoint] starting uvicorn on :${PORT:-10000}"
exec uvicorn scouting_bot.app:app --host 0.0.0.0 --port "${PORT:-10000}"
