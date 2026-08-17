"""Serving player photos the scout sent to the bot.

Photos live on Telegram's servers — the bot only stores a `file_id` (see
Prospect.photo_file_id). Showing one on a web page therefore means two calls,
getFile then a download, with the bot token attached. That token must never
reach the browser, so the dashboard proxies the image instead of linking to it.

Fetched bytes are cached in memory: a scouting photo is small, immutable for a
given file_id, and the same handful appear on every load of the home screen.
The cache is process-local and bounded; a cold start just re-fetches.

Every failure path returns None. A missing photo is not an error — the card
falls back to the player's initials.
"""

from __future__ import annotations

import httpx

from ..config import settings

_API = "https://api.telegram.org"
_TIMEOUT = 6.0
# Telegram caps bot downloads at 20 MB; a scouting photo is orders of magnitude
# smaller, so anything large is a sign we fetched the wrong thing.
_MAX_BYTES = 5 * 1024 * 1024
_CACHE_LIMIT = 64

_cache: dict[str, tuple[bytes, str]] = {}


def cached(file_id: str) -> tuple[bytes, str] | None:
    return _cache.get(file_id)


def _remember(file_id: str, payload: tuple[bytes, str]) -> None:
    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()  # bounded and cheap to rebuild; no need for true LRU
    _cache[file_id] = payload


def clear_cache() -> None:
    _cache.clear()


async def fetch(file_id: str) -> tuple[bytes, str] | None:
    """The photo's `(bytes, content_type)`, or None if it can't be served."""
    if not file_id:
        return None
    hit = _cache.get(file_id)
    if hit is not None:
        return hit
    token = settings.telegram_bot_token
    if not token:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            meta = await client.get(
                f"{_API}/bot{token}/getFile", params={"file_id": file_id}
            )
            if meta.status_code != 200:
                return None
            path = (meta.json().get("result") or {}).get("file_path")
            if not path:
                return None
            image = await client.get(f"{_API}/file/bot{token}/{path}")
            if image.status_code != 200 or len(image.content) > _MAX_BYTES:
                return None
    except (httpx.HTTPError, ValueError):
        # Telegram unreachable or answering with something that isn't JSON: the
        # page still renders, just with initials.
        return None

    payload = (image.content, image.headers.get("content-type", "image/jpeg"))
    _remember(file_id, payload)
    return payload
