#!/usr/bin/env sh
set -e

# Apply database migrations if Aerich is initialized. Best-effort: the app also
# calls generate_schemas(safe=True) on startup, so a fresh DB without a migration
# history still comes up correctly.
if [ -d "migrations" ]; then
  echo "[entrypoint] running aerich upgrade..."
  aerich upgrade || echo "[entrypoint] aerich upgrade skipped/failed (continuing; app will ensure schema)"
fi

echo "[entrypoint] starting uvicorn on :${PORT:-10000}"
exec uvicorn scouting_bot.app:app --host 0.0.0.0 --port "${PORT:-10000}"
