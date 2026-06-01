"""Scouting service — pure orchestration logic, no Telegram dependency.

Sits between the AI layer and storage. Everything here is unit-testable without
a running bot. The Telegram handlers (bot.py) are a thin shell over this.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ai.base import AIProvider, ClassifiedNote, ParsedPlayer, PlayerMatch
from .models import (
    HOME,
    Observation,
    Player,
    Session,
)
from .storage import Storage, _now


@dataclass
class CaptureResult:
    """Outcome of processing one observation note."""

    observation: Observation | None
    needs_disambiguation: bool
    candidates: list[Player]  # populated when disambiguation is needed
    classified: ClassifiedNote
    matched_player: Player | None


class ScoutingService:
    def __init__(self, storage: Storage, ai: AIProvider, confidence_threshold: float):
        self.storage = storage
        self.ai = ai
        self.threshold = confidence_threshold

    # ── Session lifecycle ───────────────────────────────────────────────
    def start_session(
        self, agent_chat_id: int, home_team: str, away_team: str, label: str | None
    ) -> tuple[Session | None, Session | None]:
        """Return (new_session, existing_active). If one is already active, don't
        create another (one active session per agent)."""
        existing = self.storage.get_active_session(agent_chat_id)
        if existing is not None:
            return None, existing
        session = self.storage.create_session(agent_chat_id, home_team, away_team, label)
        return session, None

    def end_session(self, session: Session) -> Session:
        self.storage.end_session(session.id)  # type: ignore[arg-type]
        return self.storage.get_session(session.id)  # type: ignore[arg-type]

    # ── Roster ──────────────────────────────────────────────────────────
    async def parse_and_stage_roster(
        self, image_bytes: bytes, mime_type: str
    ) -> list[ParsedPlayer]:
        return await self.ai.parse_lineup(image_bytes, mime_type)

    def save_roster(self, session: Session, parsed: list[ParsedPlayer]) -> None:
        players = [
            Player(
                id=None,
                session_id=session.id,  # type: ignore[arg-type]
                side=p.side,
                number=p.number,
                name=p.name,
                position=p.position,
            )
            for p in parsed
        ]
        self.storage.replace_roster(session.id, players)  # type: ignore[arg-type]

    def confirm_roster(self, session: Session) -> None:
        self.storage.confirm_roster(session.id)  # type: ignore[arg-type]

    # ── Capture ─────────────────────────────────────────────────────────
    async def transcribe(self, audio_bytes: bytes, mime_type: str) -> str:
        return await self.ai.transcribe_voice(audio_bytes, mime_type)

    async def capture_note(self, session: Session, text: str) -> CaptureResult:
        """Classify a note and either store it (confident) or flag for
        disambiguation (ambiguous). 'Ask only when unsure.'"""
        roster_parsed = [
            ParsedPlayer(p.number, p.name, p.position, p.side) for p in session.players
        ]
        classified = await self.ai.classify_note(text, roster_parsed)

        # Team-level note: store with no player, no disambiguation.
        if classified.is_team_note:
            obs = self._store(session, classified, player=None)
            return CaptureResult(obs, False, [], classified, None)

        matched = self._match_to_roster(session, classified.player_ref)

        # Confident + uniquely matched → store silently.
        if matched is not None and classified.confidence >= self.threshold:
            obs = self._store(session, classified, player=matched)
            return CaptureResult(obs, False, [], classified, matched)

        # Otherwise, gather candidates and ask the agent.
        candidates = self._candidates(session, classified.player_ref)
        return CaptureResult(None, True, candidates, classified, None)

    def resolve_disambiguation(
        self, session: Session, classified: ClassifiedNote, player: Player
    ) -> Observation:
        """Agent picked the player; store the previously-ambiguous note."""
        return self._store(session, classified, player=player)

    # ── Corrections ─────────────────────────────────────────────────────
    def undo_last(self, session: Session) -> Observation | None:
        last = self.storage.last_observation(session.id)  # type: ignore[arg-type]
        if last is not None:
            self.storage.delete_observation(last.id)  # type: ignore[arg-type]
        return last

    def reassign_last_player(self, session: Session, player: Player) -> bool:
        last = self.storage.last_observation(session.id)  # type: ignore[arg-type]
        if last is None:
            return False
        self.storage.update_observation(
            last.id, player_id=player.id, side=player.side  # type: ignore[arg-type]
        )
        return True

    def flip_last_sentiment(self, session: Session) -> str | None:
        last = self.storage.last_observation(session.id)  # type: ignore[arg-type]
        if last is None or last.sentiment is None:
            return None
        from .taxonomy import SENTIMENT_NEGATIVE, SENTIMENT_POSITIVE

        new = (
            SENTIMENT_NEGATIVE
            if last.sentiment == SENTIMENT_POSITIVE
            else SENTIMENT_POSITIVE
        )
        self.storage.update_observation(last.id, sentiment=new)  # type: ignore[arg-type]
        return new

    def add_missing_player(
        self, session: Session, side: str, number: int | None, name: str, position: str | None
    ) -> Player:
        """Roster gap: add a sub / missed player on the fly."""
        return self.storage.add_player(
            Player(None, session.id, side, number, name, position)  # type: ignore[arg-type]
        )

    def set_target(self, player: Player, is_target: bool) -> None:
        self.storage.set_target(player.id, is_target)  # type: ignore[arg-type]

    # ── internals ───────────────────────────────────────────────────────
    def _store(
        self, session: Session, classified: ClassifiedNote, player: Player | None
    ) -> Observation:
        obs = Observation(
            id=None,
            session_id=session.id,  # type: ignore[arg-type]
            player_id=player.id if player else None,
            side=player.side if player else None,
            sentiment=classified.sentiment,
            skill_category=classified.skill_category,
            raw_quote=classified.raw_quote,
            created_at=_now(),
        )
        return self.storage.add_observation(obs)

    def _match_to_roster(
        self, session: Session, ref: PlayerMatch | None
    ) -> Player | None:
        """Return the single roster player the reference resolves to, else None."""
        candidates = self._candidates(session, ref)
        return candidates[0] if len(candidates) == 1 else None

    def _candidates(self, session: Session, ref: PlayerMatch | None) -> list[Player]:
        if ref is None:
            return []
        players = session.players

        # Name is the strongest signal.
        if ref.name:
            by_name = [p for p in players if p.name.lower() == ref.name.lower()]
            if by_name:
                return by_name

        pool = players
        if ref.side:
            sided = [p for p in pool if p.side == ref.side]
            if sided:
                pool = sided

        if ref.number is not None:
            by_num = [p for p in pool if p.number == ref.number]
            if ref.position:
                refined = [p for p in by_num if p.position == ref.position]
                if refined:
                    return refined
            if by_num:
                return by_num

        if ref.position:
            return [p for p in pool if p.position == ref.position]

        return []
