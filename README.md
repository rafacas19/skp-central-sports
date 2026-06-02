# Live Match Scouting Bot (Telegram) — MVP

A Telegram bot that lets a football scout capture per-player observations (voice
or text) during a live match, structures them with AI, and generates a
post-match report — replacing pen-and-paper.

See [`resumen-mvp-scouting-bot.md`](resumen-mvp-scouting-bot.md) for the product
outline (ES) and [the approved plan](~/.claude/plans/) for the full MVP spec.

## What it does (MVP scope)

- **Per-match only.** One match captured well. Cross-match player database is Phase 2.
- **Roster via image + confirm.** Send a lineup photo → bot parses both teams →
  you confirm/correct before notes begin.
- **Voice or text capture.** Notes are transcribed, the player is resolved
  (number + position + name), and each note is tagged **sentiment + skill category + raw quote**.
- **Asks only when unsure.** Confident notes are logged silently; ambiguous ones
  (e.g. a jersey number shared by both teams) trigger a quick inline question.
- **Corrections any time.** `/undo`, reassign player, flip sentiment, add missing players.
- **Resilient sessions.** State lives in Postgres — a bot restart or a dropped
  phone never loses an active session; the agent just keeps sending notes.
  An auto-nudge pings sessions left open too long.
- **Report in Telegram.** A formatted summary message **+ a downloadable markdown file.**

## Architecture (one line per layer)

```
scouting_bot/
  config.py        # env-driven settings (single source, validated at load)
  taxonomy.py      # closed skill/sentiment lists (Open Question #1 lives here)
  models.py        # Tortoise ORM models — Session / Player / Observation
  db.py            # Tortoise config + init (single TORTOISE_ORM, used by Aerich too)
  storage.py       # async repository over Tortoise — the only place with queries
  ai/
    base.py        # AIProvider protocol + structured return types (the swap seam)
    mock.py        # deterministic mock — no API keys, exercises every branch
    real.py        # Claude (vision + classify) + OpenAI Whisper (transcribe)
  service.py       # async orchestration — testable without Telegram
  bot.py           # Telegram handlers (python-telegram-bot) over the service
  app.py           # FastAPI app: Telegram webhook + /healthz + read API
  __main__.py      # entrypoint (uvicorn → app.py)
migrations/        # Aerich migrations (committed)
Dockerfile         # FastAPI + uvicorn image (Python 3.11.14)
docker-compose.yml # local dev: api + postgres
```

FastAPI owns the process: the Telegram bot (python-telegram-bot) runs inside the
app's **lifespan** and receives updates via `POST /telegram/{token}`. The same
app exposes `GET /healthz` and an authed read API (`/sessions...`). The **AI is
behind a swappable interface** and ships **mocked by default**, so the whole flow
runs end-to-end with **no AI keys**. Flip `USE_MOCK_AI=false` + add keys to go live.

## Run locally (Docker Compose)

```bash
cp .env.example .env          # set TELEGRAM_BOT_TOKEN at minimum
docker compose up --build     # api on http://localhost:10000, postgres alongside
```

- `GET http://localhost:10000/healthz` → `{"status":"ok"}`
- `GET http://localhost:10000/docs` → OpenAPI UI
- Read API: `curl -H "X-API-Key: $API_KEY" http://localhost:10000/sessions`

Without `WEBHOOK_BASE_URL`, no Telegram webhook is registered (use a tunnel like
ngrok and set `WEBHOOK_BASE_URL` to receive real updates in dev). The bot fails
**soft**: if the token is bad or Telegram is unreachable, the API and health
endpoint still serve — the bot is just unavailable until fixed.

### Migrations (Aerich)

The schema is owned by Aerich migrations in `migrations/` (committed). The
container's entrypoint runs `aerich upgrade` on start; the app also calls
`generate_schemas(safe=True)` as a first-boot fallback. To create a new migration
after changing `models.py`:

```bash
docker compose run --rm api aerich migrate
docker compose run --rm api aerich upgrade
```

## Deploy to Render

This repo ships a [`render.yaml`](render.yaml) Blueprint: a managed Postgres + a
**Dockerized** FastAPI web service (webhook mode).

1. Push to GitHub, then **Render → New → Blueprint** and pick this repo.
2. Render builds the Dockerfile, creates the database, and wires `DATABASE_URL`,
   `WEBHOOK_BASE_URL` (via `RENDER_EXTERNAL_URL`), `WEBHOOK_SECRET`, `API_KEY`,
   and `PORT`. Set `TELEGRAM_BOT_TOKEN` in the dashboard.
3. It deploys mocked (`USE_MOCK_AI=true`) so you can verify the bot answers in
   Telegram with no AI keys. To go live, set `USE_MOCK_AI=false` and add
   `ANTHROPIC_API_KEY` + `OPENAI_API_KEY`, then redeploy.

Notes:
- Health check is `GET /healthz` (a real FastAPI endpoint).
- Webhook mode needs an **always-on** instance — the free tier spins down on
  idle and would drop webhooks, so the Blueprint uses the Starter plan.
- The bot registers its own webhook on startup; no manual `setWebhook` call.

## Usage (in Telegram)

```
/nuevo Boca vs River | Liga, jornada 12        # start a session
<send a photo of the lineup>                   # bot parses both teams
<tap ✅ Confirmar, or send: #10 es Pérez>       # confirm/correct roster
🎙️ "el número 8, gran control, dejó atrás a dos" # voice or text notes
/target local 10                               # optionally flag a target
/undo                                          # remove the last note
/addplayer visitante 14 Gómez CB               # roster gap (sub)
/fin                                           # → summary + report file
```

Command names and prompts are Spanish. The old English names `/newmatch` and
`/endmatch` still work as hidden aliases. `local`/`visitante` and `home`/`away`
are both accepted for the side argument.

## Tests

Tests run against a real Postgres (same engine as production), most easily via
the compose `db` service:

```bash
docker compose run --rm api pytest
```

Or point `TEST_DATABASE_URL` at any Postgres and run `pytest` directly. If no
database is reachable the suite skips with a clear message rather than failing.
Coverage: persistence (incl. one-active-session-per-agent and reload-from-disk),
note classification + disambiguation routing, corrections, roster gaps, and a
full end-to-end report render — all on the mock AI.

## Not in this MVP (Phase 2 / out of scope)

Cross-match player profiles & search, web dashboard, external CRM/fixture
integrations, multiple concurrent sessions, numeric ratings / minute tagging,
structured tactical analysis.

## Before going live — open questions for the agency

1. **Skill taxonomy** — confirm `scouting_bot/taxonomy.py` with real scouts.
2. **Report format** — markdown today; PDF is a small add if recruiters prefer it.
3. **Position taxonomy** — generic vs specific (affects disambiguation quality).
4. **Languages** — the mock handles EN/ES keywords; Whisper covers many more live.
5. **Real lineup images** — collect 5–10 real samples to validate `parse_lineup`.
