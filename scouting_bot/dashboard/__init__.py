"""Server-rendered dashboard (Spanish UI) for the scouting data.

Mounted into the main FastAPI app by `scouting_bot.app`. Pages are gated by a
shared password (DASHBOARD_PASSWORD) exchanged for an HMAC-signed session
cookie — see `auth.py`. Templates live in `templates/`, styles in `static/`.
"""

from .router import router, static_files

__all__ = ["router", "static_files"]
