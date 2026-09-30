"""Server-rendered dashboard (Spanish UI) for the scouting data.

Mounted into the main FastAPI app by `scouting_bot.app`. Pages are gated by a
shared password (DASHBOARD_PASSWORD) exchanged for an HMAC-signed session
cookie — see `auth.py`. Templates live in `templates/`, styles in `static/`.
"""

from fastapi import APIRouter

from . import pitch, profile_report, scoring
from .router import router as _pages
from .router import static_files

# The scoring routes go first: routes match in declaration order, and a literal
# path such as /partidos/nuevo must win over /partidos/{session_id}.
router = APIRouter()
router.include_router(pitch.router)
router.include_router(profile_report.router)
router.include_router(scoring.router)
router.include_router(_pages)

__all__ = ["router", "static_files"]
