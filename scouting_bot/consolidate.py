"""Consolidate scouting data from test chats into the real scout's chat.

    python -m scouting_bot.consolidate --into <chat> [--merge <chat>,<chat>] [--drop <chat>] [--apply]

Every record is keyed by a Telegram chat (`agent_chat_id`). Test accounts and,
before the dashboard had a fixed owner chat, the dashboard itself left data
under other chats. This moves it where it belongs:

  --merge  chats whose matches, players and squads move to --into. A player
           whose identity (name + club) already exists there is merged into
           that player with Storage.merge_prospects — scores, lineup places,
           squads and notes move; the target's own record wins.
  --drop   chats whose matches are deleted (with their notes, sheets and
           lineups). Their players must have no data left, or it refuses.

Without --apply nothing changes: it prints what it would do. It runs inside one
transaction, so a failure leaves the database as it was. No chat id is kept in
the code: they come from the command line.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from tortoise.transactions import in_transaction

from .db import close_db, init_db
from .models import (
    Evaluation,
    MatchPlayer,
    Observation,
    Prospect,
    ScoutProfile,
    Session,
    Squad,
    SquadMember,
)
from .storage import Storage


class Refused(Exception):
    pass


async def plan_and_run(into: int, merge: list[int], drop: list[int], apply: bool) -> list[str]:
    """Do (or describe) the consolidation; returns the report lines."""
    report: list[str] = []
    if into in merge or into in drop or set(merge) & set(drop):
        raise Refused("A chat cannot be both a source and the target, or both merged and dropped.")

    async def run() -> None:
        storage = Storage()
        for chat in merge:
            sessions = await Session.filter(agent_chat_id=chat).count()
            report.append(f"merge chat {chat}: {sessions} matches → {into}")
            if apply:
                await Session.filter(agent_chat_id=chat).update(agent_chat_id=into)

            moved = merged = 0
            for p in await Prospect.filter(agent_chat_id=chat).order_by("id"):
                twin = None
                if p.name:  # temporaries carry a per-match synthetic key: never a twin
                    twin = await Prospect.filter(
                        agent_chat_id=into, normalized_name=p.normalized_name,
                        normalized_team=p.normalized_team,
                    ).first()
                if twin is not None:
                    merged += 1
                    report.append(f"  merge player {p.id} «{p.name}» into {twin.id} «{twin.name}»")
                    if apply:
                        await storage.merge_prospects(twin.id, p.id)
                else:
                    moved += 1
                    if apply:
                        await Prospect.filter(id=p.id).update(agent_chat_id=into)
            report.append(f"  players: {moved} moved, {merged} merged into existing ones")

            for squad in await Squad.filter(agent_chat_id=chat):
                twin = await Squad.filter(
                    agent_chat_id=into, normalized_name=squad.normalized_name, category=squad.category
                ).first()
                if twin is None:
                    report.append(f"  squad {squad.id} «{squad.name}» moved")
                    if apply:
                        await Squad.filter(id=squad.id).update(agent_chat_id=into)
                else:
                    report.append(f"  squad {squad.id} «{squad.name}» merged into {twin.id}")
                    if apply:
                        have = set(await SquadMember.filter(squad_id=twin.id).values_list("prospect_id", flat=True))
                        for m in await SquadMember.filter(squad_id=squad.id):
                            if m.prospect_id not in have:
                                await SquadMember.create(squad_id=twin.id, prospect_id=m.prospect_id)
                        await Squad.filter(id=squad.id).delete()

            if await ScoutProfile.filter(agent_chat_id=chat).exists():
                report.append(f"  scout profile of chat {chat} removed (the target keeps its own)")
                if apply:
                    await ScoutProfile.filter(agent_chat_id=chat).delete()

        for chat in drop:
            ids = list(await Session.filter(agent_chat_id=chat).values_list("id", flat=True))
            obs = await Observation.filter(session_id__in=ids).count() if ids else 0
            report.append(f"drop chat {chat}: delete {len(ids)} matches ({obs} notes)")
            players = await Prospect.filter(agent_chat_id=chat)
            for p in players:
                elsewhere = (
                    await Observation.filter(prospect_id=p.id).exclude(session_id__in=ids).exists()
                    or await Evaluation.filter(prospect_id=p.id).exclude(session_id__in=ids).exists()
                    or await MatchPlayer.filter(prospect_id=p.id).exclude(session_id__in=ids).exists()
                )
                if elsewhere:
                    raise Refused(f"Player {p.id} of chat {chat} has data outside its matches; merge it instead.")
            report.append(f"  players deleted with them: {len(players)}")
            if apply:
                if ids:
                    await Session.filter(id__in=ids).delete()
                await Prospect.filter(agent_chat_id=chat).delete()
                await Squad.filter(agent_chat_id=chat).delete()
                await ScoutProfile.filter(agent_chat_id=chat).delete()

    if apply:
        async with in_transaction():
            await run()
    else:
        await run()
    report.append("APPLIED" if apply else "DRY RUN — nothing changed (add --apply)")
    return report


def _chats(raw: str | None) -> list[int]:
    return [int(x) for x in (raw or "").replace(" ", "").split(",") if x]


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--into", type=int, required=True, help="the real scout's chat id")
    parser.add_argument("--merge", default="", help="comma-separated chat ids to merge into --into")
    parser.add_argument("--drop", default="", help="comma-separated chat ids whose matches are deleted")
    parser.add_argument("--apply", action="store_true", help="actually change the data")
    args = parser.parse_args(argv)
    await init_db(generate_schemas=False)
    try:
        lines = await plan_and_run(args.into, _chats(args.merge), _chats(args.drop), args.apply)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 2
    finally:
        await close_db()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
