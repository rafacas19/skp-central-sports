"""Scouting service — pure orchestration logic, no Telegram dependency.

Sits between the AI layer and storage. Async throughout (Tortoise ORM). The
Telegram handlers (bot.py) and the REST API are thin shells over this; it stays
unit-testable without a running bot.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .ai.base import AIProvider, ClassifiedNote, PlayerMatch
from .models import Observation, Prospect, Session
from .storage import Storage

logger = logging.getLogger(__name__)

# Inline manual rating: "... valoración 7", "valoracion: 7.5", "rating 8".
_RATING_RE = re.compile(
    r"\b(?:valoraci[oó]n|rating|nota)\s*:?\s*(\d{1,2}(?:[.,]\d)?)\b", re.IGNORECASE
)
RATING_MIN, RATING_MAX = 1.0, 10.0


def extract_inline_rating(text: str) -> tuple[str, float | None]:
    """Pull a trailing manual rating out of an observation, if present.

    "Castro valoración 7" → ("Castro", 7.0). Out-of-range (1–10) is ignored.
    Returns (cleaned_text, rating|None). Pure — unit-testable on its own."""
    m = _RATING_RE.search(text)
    if not m:
        return text, None
    score = float(m.group(1).replace(",", "."))
    if not (RATING_MIN <= score <= RATING_MAX):
        return text, None
    cleaned = (text[: m.start()] + text[m.end() :]).strip(" ,.;")
    return cleaned, score


@dataclass
class CaptureResult:
    """Outcome of processing one observation note.

    Either it was stored against a prospect (``observation`` set), or it needs a
    team choice (``needs_team_choice`` — a number-only note where the scout didn't
    say which team)."""

    observation: Observation | None
    classified: ClassifiedNote
    prospect: Prospect | None
    needs_team_choice: bool = False
    team_candidates: list[str] | None = None


class ScoutingService:
    def __init__(self, storage: Storage, ai: AIProvider, confidence_threshold: float):
        self.storage = storage
        self.ai = ai
        self.threshold = confidence_threshold

    # ── Session lifecycle ───────────────────────────────────────────────
    async def start_session(
        self,
        agent_chat_id: int,
        home_team: str,
        away_team: str,
        label: str | None,
        **metadata,
    ) -> tuple[Session | None, Session | None]:
        """Return (new_session, existing_active). If one is already active, don't
        create another (one active session per agent). Optional `metadata`:
        competition / category / location / match_date. The persisted scout name
        (set via /yo) is copied onto the session for reports."""
        existing = await self.storage.get_active_session(agent_chat_id)
        if existing is not None:
            return None, existing
        scout_name = await self.storage.get_scout_name(agent_chat_id)
        session = await self.storage.create_session(
            agent_chat_id, home_team, away_team, label, scout_name=scout_name, **metadata
        )
        return session, None

    async def end_session(self, session: Session) -> Session:
        await self.storage.end_session(session.id)
        return await self.storage.get_session(session.id)

    # ── Capture ─────────────────────────────────────────────────────────
    async def transcribe(self, audio_bytes: bytes, mime_type: str) -> str:
        return await self.ai.transcribe_voice(audio_bytes, mime_type)

    async def capture_notes(
        self, session: Session, text: str, source: str = "text"
    ) -> list[CaptureResult]:
        """Classify a message (which may qualify several players) into one or more
        observations, creating/looking-up prospects on the fly. A number-only note
        with no team is flagged for a team choice. 'Ask only when unsure.'

        An inline manual rating ("Castro valoración 7") is stripped before
        classification and applied to the (single) note + its prospect."""
        text, rating = extract_inline_rating(text)
        classified_list = await self.ai.classify_notes(
            text, session.home_team, session.away_team
        )
        # An inline rating only makes sense for a single-player message.
        note_rating = rating if len(classified_list) == 1 else None
        results: list[CaptureResult] = []
        for classified in classified_list:
            results.append(
                await self._process_one(session, classified, source, note_rating)
            )
        return results

    async def _process_one(
        self,
        session: Session,
        classified: ClassifiedNote,
        source: str,
        rating: float | None = None,
    ) -> CaptureResult:
        """Resolve a classified note to a prospect and store it, or ask for a team."""
        ref = classified.player_ref

        # Team-level note: store with no prospect.
        if classified.is_team_note:
            obs = await self._store(session, classified, prospect=None, source=source)
            return CaptureResult(obs, classified, None)

        team = self._team_name(session, ref)

        # Named player → stable cross-match prospect.
        if ref and ref.name:
            prospect = await self.storage.get_or_create_prospect(
                session.agent_chat_id, ref.name, team, position=ref.position
            )
            obs = await self._store(
                session, classified, prospect=prospect, source=source, rating=rating
            )
            return CaptureResult(obs, classified, prospect)

        # Number-only with a known team → temporary, match-scoped prospect.
        if ref and ref.number is not None and team:
            prospect = await self.storage.get_or_create_temp_prospect(
                session.agent_chat_id, session.id, team, ref.number
            )
            obs = await self._store(
                session, classified, prospect=prospect, source=source, rating=rating
            )
            return CaptureResult(obs, classified, prospect)

        # Number-only with NO team → ask which team (don't guess).
        if ref and ref.number is not None:
            return CaptureResult(
                None,
                classified,
                None,
                needs_team_choice=True,
                team_candidates=[session.home_team, session.away_team],
            )

        # Position/other without a name or number, but a team is known → temp by team.
        if team:
            prospect = await self.storage.get_or_create_temp_prospect(
                session.agent_chat_id, session.id, team, ref.number if ref else None
            )
            obs = await self._store(
                session, classified, prospect=prospect, source=source, rating=rating
            )
            return CaptureResult(obs, classified, prospect)

        # Nothing to attach to → store as an unattached note rather than block.
        obs = await self._store(session, classified, prospect=None, source=source)
        return CaptureResult(obs, classified, None)

    async def resolve_team_choice(
        self, session: Session, classified: ClassifiedNote, team: str, source: str = "text"
    ) -> Observation:
        """Scout picked the team for a previously number-only note; store it."""
        number = classified.player_ref.number if classified.player_ref else None
        prospect = await self.storage.get_or_create_temp_prospect(
            session.agent_chat_id, session.id, team, number
        )
        # Pin the chosen team onto the note's identity snapshot.
        if classified.player_ref is None:
            classified.player_ref = PlayerMatch()
        classified.player_ref.team = team
        return await self._store(session, classified, prospect=prospect, source=source)

    def _team_name(self, session: Session, ref: PlayerMatch | None) -> str | None:
        """The actual team name for a note: an explicit team, else mapped from side."""
        if ref is None:
            return None
        if ref.team:
            return ref.team
        if ref.side == "home":
            return session.home_team
        if ref.side == "away":
            return session.away_team
        return None

    # ── Team notes / ratings / photos (Phase 3) ─────────────────────────
    async def add_team_note(
        self, session: Session, text: str, team: str | None
    ) -> Observation:
        side = None
        if team:
            from .taxonomy import normalize_name

            if normalize_name(team) == normalize_name(session.home_team):
                side, team = "home", session.home_team
            elif normalize_name(team) == normalize_name(session.away_team):
                side, team = "away", session.away_team
        obs = Observation(
            session_id=session.id,
            is_team_note=True,
            team=team,
            side=side,
            source="text",
            raw_quote=text,
        )
        return await self.storage.add_observation(obs)

    async def set_rating(self, prospect: Prospect, score: float) -> None:
        await self.storage.update_prospect(prospect.id, latest_rating=score)
        prospect.latest_rating = score  # keep the in-memory object consistent

    async def set_decision_by_id(self, prospect_id: int, status: str) -> None:
        await self.storage.update_prospect(prospect_id, decision_status=status)

    async def set_decision_by_name(
        self, chat_id: int, name: str, status: str
    ) -> Prospect | list[Prospect]:
        matches = await self.storage.find_prospects_by_name(chat_id, name)
        if len(matches) == 1:
            await self.storage.update_prospect(matches[0].id, decision_status=status)
            matches[0].decision_status = status
            return matches[0]
        return matches

    async def edit_prospect(
        self, chat_id: int, name: str, fields: dict
    ) -> Prospect | list[Prospect]:
        """Edit a prospect's fields by name. Renaming recomputes the identity key
        and clears the temporary flag (a named player is no longer 'unknown')."""
        from .taxonomy import normalize_identity, normalize_name

        matches = await self.storage.find_prospects_by_name(chat_id, name)
        # A temporary prospect won't be found by name (it has none); allow editing
        # the most recent temporary one when the scout is naming an unknown player.
        if not matches and "name" in fields:
            matches = await self.storage.recent_temp_prospects(chat_id)
        if len(matches) != 1:
            return matches

        prospect = matches[0]
        updates = dict(fields)
        if "name" in updates:
            updates["normalized_name"] = normalize_identity(updates["name"])
            updates["is_temporary"] = False
        if "team" in updates:
            updates["normalized_team"] = normalize_name(updates["team"] or "")
        await self.storage.update_prospect(prospect.id, **updates)
        for k, v in updates.items():
            setattr(prospect, k, v)
        return prospect

    async def detect_duplicate(
        self, chat_id: int, name: str, team: str | None, exclude_id: int
    ) -> Prospect | None:
        """A different existing prospect whose name fuzzily matches (same team)."""
        from .taxonomy import name_matches, normalize_name

        for p in await self.storage.find_prospects_by_name(chat_id, name):
            if p.id == exclude_id:
                continue
            same_team = (not team) or (
                normalize_name(p.team or "") == normalize_name(team)
            )
            if same_team and name_matches(name, p.name):
                return p
        return None

    async def merge(self, keep_id: int, drop_id: int) -> None:
        await self.storage.merge_prospects(keep_id, drop_id)

    # Cap on dedup questions asked at /finalizar (avoid flooding the scout).
    DEDUP_PAIR_CAP = 5

    async def find_dedup_pairs(
        self, session: Session
    ) -> list[tuple[Prospect, Prospect]]:
        """Pairs of DISTINCT named prospects in this match whose names fuzzily
        match and share a team — likely the same player split in two (e.g. the AI
        wrote 'Castro' once and 'Castro B.' another time, or two near-spellings).
        Each prospect appears in at most one pair; capped at DEDUP_PAIR_CAP."""
        from .taxonomy import name_matches, normalize_name

        prospects = await self.storage.prospects_in_session(session.id)
        pairs: list[tuple[Prospect, Prospect]] = []
        used: set[int] = set()
        for i, a in enumerate(prospects):
            if a.id in used:
                continue
            for b in prospects[i + 1 :]:
                if b.id in used or a.id == b.id:
                    continue
                same_team = normalize_name(a.team or "") == normalize_name(b.team or "")
                if same_team and name_matches(a.name, b.name):
                    pairs.append((a, b))  # a has the lower id (storage orders by id)
                    used.add(a.id)
                    used.add(b.id)
                    break
        if len(pairs) > self.DEDUP_PAIR_CAP:
            logger.warning(
                "session %s: %d dedup pairs found, asking only %d",
                session.id, len(pairs), self.DEDUP_PAIR_CAP,
            )
            pairs = pairs[: self.DEDUP_PAIR_CAP]
        return pairs

    async def rate_by_name(
        self, chat_id: int, name: str, score: float
    ) -> Prospect | list[Prospect]:
        """Apply a rating by player name. Returns the rated prospect, or a list of
        candidates when the name is ambiguous (so the caller can ask)."""
        matches = await self.storage.find_prospects_by_name(chat_id, name)
        if len(matches) == 1:
            await self.set_rating(matches[0], score)  # mutates in place
            return matches[0]
        return matches  # 0 or >1 → caller handles

    async def attach_photo(
        self, session: Session, team: str | None, file_id: str
    ) -> Prospect:
        """Create a temporary unknown-player prospect carrying a photo file_id."""
        prospect = await self.storage.get_or_create_temp_prospect(
            session.agent_chat_id, session.id, team, None
        )
        await self.storage.update_prospect(prospect.id, photo_file_id=file_id)
        prospect.photo_file_id = file_id  # keep the in-memory object consistent
        return prospect

    async def player_report(
        self, chat_id: int, name: str
    ) -> tuple[Prospect, list[Observation], str] | list[Prospect]:
        """Build the inputs for an accumulated player report. Returns
        (prospect, observations, ai_summary), or a list of candidates when the
        name is ambiguous / not found (the caller asks or reports 'none')."""
        matches = await self.storage.find_prospects_by_name(chat_id, name)
        if len(matches) != 1:
            return matches
        prospect = matches[0]
        observations = await self.storage.observations_for_prospect(
            chat_id, prospect.id
        )
        payload = [self._obs_to_dict(o) for o in observations]
        summary = await self.ai.summarize_player(payload)
        return prospect, observations, summary

    def _obs_to_dict(self, o: Observation) -> dict:
        session = getattr(o, "session", None)
        team = o.team or ""
        return {
            "date": o.created_at.strftime("%Y-%m-%d") if o.created_at else "",
            "match": (
                f"{session.home_team} vs {session.away_team}" if session else ""
            ),
            "team": team,
            "opponent": self._opponent(session, team) if session else "",
            "position": o.player_position or "",
            "number": o.player_number,
            "observation": o.raw_quote,
            "rating": o.rating,
            "source": o.source or "",
            "scout": (session.scout_name if session else None) or "",
        }

    @staticmethod
    def _opponent(session: Session, team: str) -> str:
        from .taxonomy import normalize_name

        if not team:
            return ""
        nt = normalize_name(team)
        if nt == normalize_name(session.home_team):
            return session.away_team
        if nt == normalize_name(session.away_team):
            return session.home_team
        return ""

    async def capture_to_prospect(
        self, session: Session, text: str, prospect: Prospect, source: str = "text"
    ) -> Observation:
        """Store an observation directly against a known prospect (e.g. the temp
        profile created by /foto), bypassing identity resolution."""
        from .ai.base import ClassifiedNote, PlayerMatch

        classified = ClassifiedNote(
            raw_quote=text,
            is_team_note=False,
            player_ref=PlayerMatch(team=prospect.team),
            confidence=1.0,
        )
        return await self._store(session, classified, prospect=prospect, source=source)

    # ── Corrections ─────────────────────────────────────────────────────
    async def undo_last(self, session: Session) -> Observation | None:
        last = await self.storage.last_observation(session.id)
        if last is not None:
            await self.storage.delete_observation(last.id)
        return last

    # ── internals ───────────────────────────────────────────────────────
    async def _store(
        self,
        session: Session,
        classified: ClassifiedNote,
        prospect: Prospect | None,
        source: str,
        rating: float | None = None,
    ) -> Observation:
        ref = classified.player_ref
        team = self._team_name(session, ref)
        obs = Observation(
            session_id=session.id,
            prospect_id=prospect.id if prospect else None,
            is_team_note=classified.is_team_note,
            team=team,
            player_name=(ref.name if ref else None),
            player_number=(ref.number if ref else None),
            player_position=(ref.position if ref else None),
            source=source,
            rating=rating,
            raw_quote=classified.raw_quote,
        )
        stored = await self.storage.add_observation(obs)
        # An inline rating also updates the prospect's latest rating.
        if rating is not None and prospect is not None:
            await self.storage.update_prospect(prospect.id, latest_rating=rating)
            prospect.latest_rating = rating  # keep the in-memory object consistent
        return stored
