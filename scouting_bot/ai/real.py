"""Real AI provider: Claude (vision + classification) + OpenAI Whisper (transcription).

Only imported when USE_MOCK_AI=false, so the SDKs are an optional dependency for
the MVP. The bot logic is identical to the mock path — same return types.
"""

from __future__ import annotations

import io
import json

from ..config import settings
from .base import AIProvider, ClassifiedNote, PlayerMatch

_CLASSIFY_PROMPT = """Eres un asistente de scouting de fútbol. Extraes la
IDENTIDAD del jugador de un mensaje de observación en vivo. NO evalúes ni
califiques: solo identifica de quién habla la nota.

Partido: "{home}" (local) vs "{away}" (visitante).

Mensaje: "{text}"

Un mensaje puede comentar a varios jugadores (p. ej. "el 10 muy bien pero el 4
lento"). Emite UNA nota por cada jugador o nota de equipo distinta.

SUSTITUCIONES: si el mensaje describe un cambio ("entra X y sale Y", "entra el
7 sale Ocampo"), emite UNA sola nota con is_substitution=true y player_ref del
jugador que ENTRA (nunca del que sale). Lo más importante es identificar a quién
entra, porque el scout hará observaciones posteriores sobre él. Si el que entra
es un número sin equipo, deja team/side en null (el bot preguntará).

Devuelve JSON ESTRICTO: {{"notes": [ <note>, ... ]}} donde cada <note> tiene:
- raw_quote: la frase del mensaje que corresponde a esta nota (el mensaje
  completo si es una sola observación).
- is_team_note: true si habla del equipo/táctica, no de un jugador concreto.
- is_substitution: true si es un cambio (entra/sale). Por defecto false.
- player_ref: {{"number":int|null,"name":str|null,"position":str|null,"team":str|null,"side":"home"|"away"|null}}
  - name: SOLO los tokens de nombre/apellido que el scout escribió textualmente.
    NUNCA inventes, expandas ni añadas iniciales, segundos apellidos, acentos ni
    puntuación que el scout no haya dicho. Si el scout solo dijo el apellido,
    devuelve únicamente ese apellido (p. ej. si dijo "Castro", devuelve "Castro",
    nunca "Castro B." ni "C. Castro"). Si no se menciona ningún nombre, null.
  - team: el nombre EXACTO del equipo ("{home}" o "{away}") si el scout lo dice.
  - side: "home" si team es "{home}", "away" si es "{away}", si no null.
- confidence: 0.0-1.0. Usa < 0.6 cuando la referencia es ambigua, sobre todo un
  número SIN equipo (podría ser de cualquiera de los dos equipos). Un nombre, o
  un número con equipo indicado, es alta confianza.
Si el mensaje no contiene ninguna observación clasificable devuelve {{"notes": []}}.

Devuelve SOLO el objeto JSON."""

_SUMMARY_PROMPT = """Eres un analista de scouting. A partir del historial de
observaciones en bruto de un jugador (en varios partidos), redacta un perfil
breve en español: patrones recurrentes, fortalezas, posibles dudas y una
recomendación final. No inventes datos que no estén en las observaciones.

Observaciones (JSON):
{observations}

Devuelve solo el texto del perfil, sin encabezados ni JSON."""


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

    async def classify_notes(
        self, text: str, home_team: str, away_team: str
    ) -> list[ClassifiedNote]:
        prompt = _CLASSIFY_PROMPT.format(
            home=home_team,
            away=away_team,
            text=text.replace('"', "'"),
        )
        msg = await self._claude.messages.create(
            model=settings.anthropic_model,
            max_tokens=1200,  # room for several notes from one message
            messages=[{"role": "user", "content": prompt}],
        )
        data = _extract_json(msg.content[0].text)
        return [_note_from(raw, text) for raw in data.get("notes", [])]

    async def summarize_player(self, observations: list[dict]) -> str:
        prompt = _SUMMARY_PROMPT.format(observations=json.dumps(observations, ensure_ascii=False))
        msg = await self._claude.messages.create(
            model=settings.anthropic_model,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()


def _note_from(raw: dict, full_text: str) -> ClassifiedNote:
    """Build a ClassifiedNote from one note object in the model's `notes` array."""
    is_team = bool(raw.get("is_team_note"))
    ref_raw = raw.get("player_ref")
    ref = None
    if ref_raw:
        ref = PlayerMatch(
            number=_to_int(ref_raw.get("number")),
            name=ref_raw.get("name"),
            position=ref_raw.get("position"),
            side=ref_raw.get("side"),
            team=ref_raw.get("team"),
        )
    quote = raw.get("raw_quote") or full_text
    return ClassifiedNote(
        raw_quote=quote,
        is_team_note=is_team,
        player_ref=ref,
        confidence=float(raw.get("confidence", 0.5)),
        is_substitution=bool(raw.get("is_substitution")),
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
