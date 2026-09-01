#!/usr/bin/env python3
"""Split existing team names into club + category (the production catch-up pass).

New rows are already split at capture time; this is the one-off pass over the
rows written before that. It rewrites team names ("Santa Fe U18" → "Santa Fe"),
fills the category columns, and MERGES prospects that become the same player
once their team name loses the category (keeping the oldest record).

    python scripts/backfill_categories.py            # dry run — prints, writes nothing
    python scripts/backfill_categories.py --apply    # actually writes

Inside the compose stack:

    docker compose run --rm api python scripts/backfill_categories.py
    docker compose run --rm api python scripts/backfill_categories.py --apply

Reads DATABASE_URL like the app does, so pointing it at production is a matter
of exporting that variable — run the dry run first and read the merges, they are
not reversible. Run it with no match in progress: a live session capturing notes
while names are being rewritten can attach a note to the pre-merge record.
Re-running is safe (an already-split name derives nothing).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scouting_bot.backfill import apply_backfill, format_plan, plan_backfill  # noqa: E402
from scouting_bot.db import close_db, init_db  # noqa: E402


async def _run(apply: bool) -> int:
    await init_db()
    try:
        plan = await plan_backfill()
        print(format_plan(plan))
        if plan.empty:
            return 0
        if not apply:
            print("\nSimulación: no se ha escrito nada. Repite con --apply para aplicarlo.")
            return 0
        if plan.active_sessions:
            print(
                f"\n⚠ Hay {plan.active_sessions} partido(s) activo(s). Ciérralos "
                "(/fin) antes de aplicar el backfill.",
                file=sys.stderr,
            )
            return 1
        await apply_backfill(plan)
        print(
            f"\n✅ Aplicado: {len(plan.sessions)} partido(s), "
            f"{len(plan.prospects)} jugador(es), {len(plan.merges)} fusión(es)."
        )
        return 0
    finally:
        await close_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default: dry run, prints the plan only)",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
