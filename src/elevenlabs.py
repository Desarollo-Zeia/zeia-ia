"""Cliente mínimo de ElevenLabs: STT (Scribe) y TTS (Flash v2.5).

Usa httpx (ya instalado como dependencia de openai). Si falta la API key o
ElevenLabs responde con error, se lanza ElevenLabsError con mensaje legible.
"""
from __future__ import annotations

import re

import httpx

from . import config

BASE_URL = "https://api.elevenlabs.io/v1"
TIMEOUT = 60.0
# La respuesta hablada es un resumen natural; la completa queda en pantalla.
MAX_TTS_CHARS = 1500


class ElevenLabsError(RuntimeError):
    """Error legible de ElevenLabs (falta de key, cuota, formato, etc.)."""


def _key() -> str:
    if not config.ELEVENLABS_API_KEY:
        raise ElevenLabsError("Falta ELEVENLABS_API_KEY en .env")
    return config.ELEVENLABS_API_KEY


def _check(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    detail = resp.text[:300]
    try:
        d = resp.json().get("detail")
        detail = d.get("message") if isinstance(d, dict) else str(d)
    except Exception:
        pass
    raise ElevenLabsError(f"ElevenLabs respondió {resp.status_code}: {detail}")


def transcribe(audio: bytes, filename: str = "voz.webm") -> str:
    """Audio → texto (Scribe v1, español). Acepta webm/opus, mp3, wav, m4a, ogg."""
    files = {"file": (filename, audio)}
    data = {"model_id": config.ELEVENLABS_STT_MODEL, "language_code": "es"}
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(
            f"{BASE_URL}/speech-to-text",
            headers={"xi-api-key": _key()},
            files=files,
            data=data,
        )
    _check(resp)
    return (resp.json().get("text") or "").strip()


def clean_for_speech(text: str, max_chars: int = MAX_TTS_CHARS) -> str:
    """Quita markdown/tablas/emojis para que la voz suene natural."""
    t = re.sub(r"```.*?```", " ", text, flags=re.S)            # bloques de código
    t = re.sub(r"^.*\|.*$", " ", t, flags=re.M)                # filas de tabla
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)             # links → texto
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)               # encabezados
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)                   # negritas
    t = re.sub(r"`([^`]+)`", r"\1", t)                         # código inline
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.M)             # viñetas
    t = re.sub(r"[^\w\s.,;:()%$€/\-+¿?¡!]", "", t)             # emojis y símbolos
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(" ", 1)[0] + ". La respuesta completa está en pantalla."
    return t


def synthesize(text: str, voice_id: str | None = None) -> bytes:
    """Texto → audio MP3 (eleven_flash_v2_5: buena calidad y baja latencia)."""
    payload = {
        "text": text,
        "model_id": config.ELEVENLABS_TTS_MODEL,
        "language_code": "es",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(
            f"{BASE_URL}/text-to-speech/{voice_id or config.ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": _key(), "Content-Type": "application/json"},
            json=payload,
        )
    _check(resp)
    return resp.content
