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
- **Resilient sessions.** State lives in SQLite — a bot restart or a dropped
  phone never loses an active session; the agent just keeps sending notes.
  An auto-nudge pings sessions left open too long.
- **Report in Telegram.** A formatted summary message **+ a downloadable markdown file.**

## Architecture (one line per layer)

```
scouting_bot/
  config.py        # env-driven settings (single source)
  taxonomy.py      # closed skill/sentiment lists (Open Question #1 lives here)
  models.py        # Session / Player / Observation dataclasses
  storage.py       # SQLite repository — the only SQL; swap to Postgres in Phase 2
  ai/
    base.py        # AIProvider protocol + structured return types (the swap seam)
    mock.py        # deterministic mock — no API keys, exercises every branch
    real.py        # Claude (vision + classify) + OpenAI Whisper (transcribe)
  service.py       # pure orchestration — testable without Telegram
  bot.py           # thin Telegram shell over the service
  __main__.py      # entrypoint
```

The **AI is behind a swappable interface** and ships **mocked by default**, so the
whole flow runs end-to-end with **no API keys**. Flip `USE_MOCK_AI=false` and add
keys to go live.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then edit
```

At minimum set `TELEGRAM_BOT_TOKEN` (from @BotFather). Leave `USE_MOCK_AI=true`
to run without AI keys; set it to `false` and fill `ANTHROPIC_API_KEY` +
`OPENAI_API_KEY` for real transcription/vision/classification.

## Run

```bash
python -m scouting_bot
```

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

```bash
pytest
```

Covers persistence (incl. one-active-session-per-agent and reload-from-disk),
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
