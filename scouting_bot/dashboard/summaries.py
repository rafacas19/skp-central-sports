"""Cached per-player AI summaries for the profile page (stale-while-revalidate).

Cost model: one LLM call per player per new batch of observations, never per
view. The cache lives on the prospect row (`ai_summary`) together with the
observation count it was generated from (`ai_summary_obs_count`); a drifted
count marks the summary stale.

Latency model: a page never waits for the LLM except the very first view of a
player. A stale summary is served as-is and refreshed after the response via
BackgroundTasks — the next visit shows the updated text. Two overlapping
requests can at worst refresh twice (same result); with a single scout that
race isn't worth a lock.
"""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks

from ..ai import get_provider
from ..models import Observation, Prospect
from ..service import obs_to_summary_dict

logger = logging.getLogger(__name__)


async def get_or_refresh(prospect_id: int, background: BackgroundTasks) -> dict:
    """The summary state for a profile view: {"text", "refreshing", "failed"}."""
    none = {"text": None, "refreshing": False, "failed": False}
    prospect = await Prospect.get_or_none(id=prospect_id)
    if prospect is None:
        return none
    count = await Observation.filter(prospect_id=prospect_id).count()
    if count == 0:
        return none  # nothing to summarize

    if prospect.ai_summary and prospect.ai_summary_obs_count == count:
        return {"text": prospect.ai_summary, "refreshing": False, "failed": False}

    if prospect.ai_summary:
        # Stale: serve immediately, refresh after the response is sent.
        background.add_task(_refresh_quietly, prospect_id)
        return {"text": prospect.ai_summary, "refreshing": True, "failed": False}

    # First-ever view: generate inline (the one case a view waits on the LLM).
    try:
        return {"text": await refresh(prospect_id), "refreshing": False, "failed": False}
    except Exception:  # noqa: BLE001 — a broken AI must never break the page
        logger.exception("AI summary generation failed for prospect %s", prospect_id)
        return {"text": None, "refreshing": False, "failed": True}


async def refresh(prospect_id: int) -> str:
    """Generate and store the summary from the player's full history."""
    observations = await (
        Observation.filter(prospect_id=prospect_id)
        .order_by("created_at", "id")
        .prefetch_related("session")
    )
    payload = [obs_to_summary_dict(o) for o in observations]
    text = await get_provider().summarize_player(payload)
    await Prospect.filter(id=prospect_id).update(
        ai_summary=text, ai_summary_obs_count=len(observations)
    )
    return text


async def _refresh_quietly(prospect_id: int) -> None:
    try:
        await refresh(prospect_id)
    except Exception:  # noqa: BLE001 — background failure just keeps the stale text
        logger.exception("Background AI summary refresh failed for prospect %s", prospect_id)
