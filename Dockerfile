# syntax=docker/dockerfile:1
FROM python:3.11.14-slim

# Faster, quieter, no .pyc clutter.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source.
COPY scouting_bot ./scouting_bot
COPY pyproject.toml entrypoint.sh ./
# migrations/ is created by `aerich init-db` and committed; copy if present.
COPY migrations ./migrations
RUN chmod +x entrypoint.sh

# Render injects $PORT; default for local runs.
ENV PORT=10000
EXPOSE 10000

# Apply migrations (best-effort) then serve. See entrypoint.sh.
CMD ["./entrypoint.sh"]
