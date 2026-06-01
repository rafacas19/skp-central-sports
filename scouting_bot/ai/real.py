"""Real AI provider: Claude (vision + classification) + OpenAI Whisper (transcription).

Only imported when USE_MOCK_AI=false, so the SDKs are an optional dependency for
the MVP. The bot logic is identical to the mock path — same return types.
"""

from __future__ import annotations

import base64
import io
import json

from ..config import settings
from ..taxonomy import SKILL_CATEGORIES, normalize_sentiment, normalize_skill
from .base import AIProvider, ClassifiedNote, ParsedPlayer, PlayerMatch

_LINEUP_PROMPT = """You are reading a football match lineup image.
Extract every visible player for BOTH teams. Return STRICT JSON:
{"players": [{"number": int|null, "name": str, "position": str|null, "side": "home"|"away"}]}
Use "home" for the first/left team and "away" for the second/right team.
If you are unsure of a value use null. Return ONLY the JSON object."""

_CLASSIFY_PROMPT = """You classify a single live football scouting note.

Roster (the only players that exist in this match):
{roster}

Note: "{text}"

Decide:
- is_team_note: true if it is about a team/tactics, not a specific player.
- sentiment: "positive" or "negative" (null if team note).
- skill_category: one of {skills} (null if team note).
- player_ref: the player it refers to, as {{"number":int|null,"name":str|null,"position":str|null,"side":"home"|"away"|null}} (null if team note or unknown).
- confidence: 0.0-1.0 — how sure you are about player_ref. Use < 0.6 when the
  reference is ambiguous (e.g. a jersey number shared by both teams, or a vague
  "the tall one").

Return STRICT JSON with exactly these keys. Return ONLY the JSON object."""


class RealAIProvider(AIProvider):
    def __init__(self) -> None:
        import anthropic
        from openai import AsyncOpenAI

        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required when USE_MOCK_AI=false")
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when USE_MOCK_AI=false")

        self._claude = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._openai = AsyncOpenAI(api_key=settings.openai_api_key)

    async def transcribe_voice(self, audio_bytes: bytes, mime_type: str) -> str:
        buf = io.BytesIO(audio_bytes)
        buf.name = "voice.ogg"  # Telegram voice notes are OGG/Opus
        resp = await self._openai.audio.transcriptions.create(
            model=settings.openai_transcribe_model,
            file=buf,
        )
        return resp.text.strip()

    async def parse_lineup(
        self, image_bytes: bytes, mime_type: str
    ) -> list[ParsedPlayer]:
        b64 = base64.standard_b64encode(image_bytes).decode()
        msg = await self._claude.messages.create(
            model=settings.anthropic_model,
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": _LINEUP_PROMPT},
                    ],
                }
            ],
        )
        data = _extract_json(msg.content[0].text)
        players: list[ParsedPlayer] = []
        for p in data.get("players", []):
            side = "away" if str(p.get("side", "home")).lower().startswith("a") else "home"
            players.append(
                ParsedPlayer(
                    number=_to_int(p.get("number")),
                    name=str(p.get("name", "")).strip() or "Unknown",
                    position=p.get("position"),
                    side=side,
                )
            )
        return players

    async def classify_note(
        self, text: str, roster: list[ParsedPlayer]
    ) -> ClassifiedNote:
        roster_str = "\n".join(
            f"- {p.side} #{p.number} {p.name} ({p.position})" for p in roster
        )
        prompt = _CLASSIFY_PROMPT.format(
            roster=roster_str or "(empty)",
            text=text.replace('"', "'"),
            skills=", ".join(SKILL_CATEGORIES),
        )
        msg = await self._claude.messages.create(
            model=settings.anthropic_model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        data = _extract_json(msg.content[0].text)

        is_team = bool(data.get("is_team_note"))
        ref_raw = data.get("player_ref")
        ref = None
        if ref_raw and not is_team:
            ref = PlayerMatch(
                number=_to_int(ref_raw.get("number")),
                name=ref_raw.get("name"),
                position=ref_raw.get("position"),
                side=ref_raw.get("side"),
            )

        return ClassifiedNote(
            raw_quote=text,
            is_team_note=is_team,
            sentiment=None if is_team else normalize_sentiment(data.get("sentiment")),
            skill_category=None if is_team else normalize_skill(data.get("skill_category")),
            player_ref=ref,
            confidence=float(data.get("confidence", 0.5)),
        )


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _to_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
