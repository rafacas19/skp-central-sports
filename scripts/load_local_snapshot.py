#!/usr/bin/env python3
"""Load a snapshot of the production data into the LOCAL dev database.

Useful because the test suite truncates the local tables (see tests/conftest.py),
so a `pytest` run wipes whatever you were looking at in the dashboard. Re-running
this puts it back.

    python scripts/load_local_snapshot.py --fetch   # pull fresh from Render, then load
    python scripts/load_local_snapshot.py           # load the newest local snapshot

Fetching needs the `render` CLI on PATH plus RENDER_API_KEY and SCOUTING_DB_ID
(from the environment or ~/.render_env). Snapshots are written under reports/,
which is git-ignored — they contain real player data and must never be committed.

Production is only ever read (COPY … TO STDOUT). The load side refuses to run
against anything that isn't the local compose database, so this cannot overwrite
prod by accident.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

# Parents before children: observations reference sessions and prospects.
TABLES = ("sessions", "scout_profiles", "prospects", "observations")
SEQUENCED = ("sessions", "prospects", "observations")  # scout_profiles is keyed by chat id

DB_ID_VAR = "SCOUTING_DB_ID"
COMPOSE_DB = ["docker", "compose", "exec", "-T", "db"]
PSQL = ["psql", "-U", "scouting", "-d", "scouting", "-v", "ON_ERROR_STOP=1"]
REPORTS = Path("reports")


def _load_render_env() -> None:
    """Populate RENDER_API_KEY / SCOUTING_DB_ID from ~/.render_env if unset."""
    path = os.path.expanduser("~/.render_env")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            key, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and key in ("RENDER_API_KEY", DB_ID_VAR) and not os.environ.get(key):
                os.environ[key] = value.strip().strip("'\"")


def fetch(destination: Path) -> Path:
    """Export every table from the production database as CSV. Read-only."""
    _load_render_env()
    db_id = os.environ.get(DB_ID_VAR)
    if not os.environ.get("RENDER_API_KEY") or not db_id:
        sys.exit(
            f"Need RENDER_API_KEY and {DB_ID_VAR} to fetch "
            "(set them in the environment or ~/.render_env)."
        )
    destination.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        result = subprocess.run(
            ["render", "psql", db_id, "--confirm", "--command",
             f"COPY (SELECT * FROM {table} ORDER BY 1) TO STDOUT WITH CSV HEADER"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.exit(f"render psql failed for {table}:\n{result.stderr.strip()}")
        (destination / f"{table}.csv").write_text(result.stdout)
        print(f"  fetched {table}")
    return destination


def newest_snapshot() -> Path:
    """The most recent snapshot directory holding all four tables."""
    candidates = [
        d for d in sorted(REPORTS.glob("*"), reverse=True)
        if d.is_dir() and all((d / f"{t}.csv").exists() for t in TABLES)
    ]
    if not candidates:
        sys.exit(
            "No snapshot found under reports/. Run with --fetch to pull one from Render."
        )
    return candidates[0]


def _psql(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        COMPOSE_DB + PSQL + args, input=stdin, capture_output=True, text=True
    )
    if result.returncode != 0:
        sys.exit(f"psql failed:\n{result.stderr.strip()}")
    return result


def _assert_local() -> None:
    """Refuse to touch anything but the local compose database.

    Routing every statement through `docker compose exec db` already makes it
    impossible to reach Render; this is the second check, and it also gives a
    clear message when the stack simply isn't running."""
    probe = subprocess.run(
        COMPOSE_DB + PSQL + ["-tAc", "SELECT current_database()"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        sys.exit(
            "Can't reach the local database. Is the stack up?  docker compose up -d\n"
            + probe.stderr.strip()
        )
    name = probe.stdout.strip()
    if name != "scouting":
        sys.exit(f"Refusing to load: unexpected target database {name!r}.")


def load(source: Path) -> None:
    _assert_local()
    _psql(["-c", "TRUNCATE observations, prospects, scout_profiles, sessions "
                 "RESTART IDENTITY CASCADE;"])
    for table in TABLES:
        text = (source / f"{table}.csv").read_text()
        header, _, rows = text.partition("\n")
        if not rows.strip():
            print(f"  {table}: empty")
            continue
        # Copy by explicit column list, so a snapshot taken before a migration
        # still loads — columns the export predates simply stay NULL.
        out = _psql(["-c", f"COPY {table} ({header.strip()}) FROM STDIN WITH CSV"], rows)
        print(f"  {table}: {out.stdout.strip() or 'loaded'}")

    for table in SEQUENCED:
        _psql(["-tAc", f"SELECT setval(pg_get_serial_sequence('{table}','id'), "
                       f"COALESCE((SELECT MAX(id) FROM {table}), 1));"])

    counts = _psql(["-tAc", "SELECT " + ", ".join(
        f"(SELECT count(*) FROM {t})" for t in TABLES)]).stdout.strip()
    print("Loaded (" + ", ".join(TABLES) + "): " + counts.replace("|", ", "))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true",
                    help="pull a fresh snapshot from production first (read-only)")
    ap.add_argument("--dir", help="snapshot directory to load (default: newest under reports/)")
    args = ap.parse_args()

    if args.fetch:
        source = fetch(REPORTS / f"prod-snapshot-{date.today().isoformat()}")
    elif args.dir:
        source = Path(args.dir)
        if not source.is_dir():
            sys.exit(f"No such directory: {source}")
    else:
        source = newest_snapshot()

    print(f"Loading from {source}")
    load(source)


if __name__ == "__main__":
    main()
