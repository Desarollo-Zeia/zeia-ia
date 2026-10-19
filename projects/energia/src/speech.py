"""Resumen hablado de las respuestas de ZEIA.

El cliente quiere datos concretos y rápidos: la voz NO lee la respuesta
completa (tablas, markdown, secciones), sino una versión conversada de 1-3
frases con las cifras clave. El detalle y los gráficos quedan en pantalla.
"""
from __future__ import annotations

from openai import OpenAI

from . import config
from .elevenlabs import clean_for_speech

_PROMPT_HEAD = """Eres la VOZ de ZEIA, un asistente de análisis energético en una
conversación con el cliente. Convierte la respuesta completa en un mensaje
HABLADO para decirle directamente:

- 1 a 3 frases, directas y conversacionales, en español.
- Conserva las cifras clave con su unidad (kWh, kW, S/) y el periodo,
  redondeando decimales innecesarios.
- NO alteres cifras ni unidades: dilas tal como aparecen (si dice kWh no
  digas kW; si dice 310 no digas 306). Ante la duda, omite el dato.
- Si hay un hallazgo accionable relevante, ciérralo en una frase.
- Si la respuesta dice que no hay datos, dilo en una frase y da la alternativa.
- PROHIBIDO: markdown, tablas, emojis, viñetas, encabezados, frases como
  "según la consulta" o "en la base de datos".

Respuesta completa:
\"\"\"
"""

_PROMPT_TAIL = """
\"\"\"

Mensaje hablado (solo el texto a decir, sin comillas):"""


def summarize_for_voice(answer: str) -> str:
    """Versión corta y hablable de la respuesta; fallback = limpieza simple."""
    if not answer.strip():
        return ""
    try:
        client = OpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
        )
        resp = client.chat.completions.create(
            model=config.VOICE_SUMMARY_MODEL,
            messages=[
                {"role": "user", "content": _PROMPT_HEAD + answer[:6000] + _PROMPT_TAIL}
            ],
            temperature=0.2,
            max_tokens=220,
        )
        text = (resp.choices[0].message.content or "").strip().strip('"').strip()
        return clean_for_speech(text, max_chars=800) or clean_for_speech(answer)
    except Exception:
        # Sin resumen del LLM, al menos limpiar el markdown.
        return clean_for_speech(answer)
