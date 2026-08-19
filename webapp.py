#!/usr/bin/env python
"""App web del agente de energía: FastAPI + UI de chat con gráficos.

Uso:
    python webapp.py           → http://localhost:8000
    PORT=8080 python webapp.py
"""
from __future__ import annotations

import os
import sys
import uuid

# En Windows la consola usa cp1252; forzar UTF-8 para imprimir "→" y demás.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel
import psycopg2

from src import config, elevenlabs, speech
from src.agent import EnergyAgent

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web" / "static"

# Modelos ofrecidos en el selector (resultado de la evaluación comparativa)
MODEL_OPTIONS = [
    {"id": "qwen/qwen3-coder", "label": "Qwen 3 Coder (recomendado)"},
    {"id": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash (rápido/barato)"},
    {"id": "google/gemini-2.5-flash", "label": "Gemini 2.5 Flash (rápido)"},
    {"id": "openai/gpt-4.1-mini", "label": "GPT-4.1 mini"},
    {"id": "anthropic/claude-sonnet-4.5", "label": "Claude Sonnet 4.5 (premium)"},
]

app = FastAPI(title="ZEIA - Agente de energía")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_sessions: dict = {}


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = None


class TTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None


class SpeakRequest(BaseModel):
    text: str


def _to_number(v):
    """Convierte a float tolerando '2,436.0', 'S/ 123', None, etc."""
    if v is None or isinstance(v, (int, float)):
        return v
    try:
        s = str(v).replace(",", "").replace("S/", "").strip()
        return float(s) if s not in ("", "-", "null", "None") else None
    except (TypeError, ValueError):
        return None


def sanitize_chart(spec: dict) -> Optional[dict]:
    """Normaliza un spec de gráfico; devuelve None si es irrecuperable."""
    if not isinstance(spec, dict):
        return None
    chart_type = spec.get("chart_type")
    series = spec.get("series")
    if chart_type not in ("line", "bar", "area", "pie") or not isinstance(series, list) or not series:
        return None
    clean_series = []
    for s in series:
        if not isinstance(s, dict) or not isinstance(s.get("data"), list):
            continue
        clean_series.append({
            "name": str(s.get("name", "")),
            "data": [_to_number(v) for v in s["data"]],
        })
    if not clean_series or all(all(v is None for v in s["data"]) for s in clean_series):
        return None
    x = spec.get("x")
    if not isinstance(x, list):
        x = []
    return {
        "chart_type": chart_type,
        "title": str(spec.get("title", "")),
        "x": [str(v) for v in x],
        "series": clean_series,
        "y_unit": str(spec.get("y_unit", "") or ""),
    }


def get_agent(session_id: str, model: Optional[str]) -> EnergyAgent:
    agent = _sessions.get(session_id)
    if agent is None:
        agent = EnergyAgent(model=model or config.DEFAULT_MODEL)
        _sessions[session_id] = agent
    elif model and model != agent.model:
        # Cambio de modelo: conservar historial, nuevo cliente al mismo endpoint
        agent.model = model
        agent.client = OpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
        )
    return agent


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/models")
def models():
    return {"default": config.DEFAULT_MODEL, "options": MODEL_OPTIONS}


@app.post("/api/chat")
def chat(req: ChatRequest):
    session_id = req.session_id or uuid.uuid4().hex
    if not req.message.strip():
        raise HTTPException(400, "Mensaje vacío")
    valid_models = {m["id"] for m in MODEL_OPTIONS}
    model = req.model if req.model in valid_models else None
    agent = get_agent(session_id, model)
    result = agent.ask(req.message)
    charts = [c for c in (sanitize_chart(s) for s in result.charts) if c]
    return {
        "session_id": session_id,
        "answer": result.answer,
        "charts": charts,
        "queries": result.queries,
        "usage": result.usage,
        "error": result.error,
    }


@app.post("/api/reset")
def reset(req: ChatRequest):
    if req.session_id and req.session_id in _sessions:
        _sessions[req.session_id].reset()
    return {"ok": True}


# --- Voz (ElevenLabs) -------------------------------------------------------

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB (≈ varios minutos de webm/opus)

_EXT_BY_MIME = {
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
    "audio/flac": "flac",
}


@app.get("/api/voice/status")
def voice_status():
    """La UI lo consulta al cargar: sin API key, deshabilita el micrófono."""
    return {"enabled": bool(config.ELEVENLABS_API_KEY)}


@app.post("/api/voice/transcribe")
async def voice_transcribe(request: Request):
    """Audio crudo en el body (Content-Type: audio/…) → {"text": ...}."""
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "Audio vacío")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio demasiado grande (máx 10 MB)")
    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = _EXT_BY_MIME.get(mime, "webm")
    try:
        text = elevenlabs.transcribe(audio, f"voz.{ext}")
    except elevenlabs.ElevenLabsError as e:
        raise HTTPException(502, str(e))
    return {"text": text}


@app.post("/api/voice/tts")
def voice_tts(req: TTSRequest):
    """Texto → audio/mpeg. El markdown se limpia para que suene natural."""
    spoken = elevenlabs.clean_for_speech(req.text)
    if not spoken:
        raise HTTPException(400, "Nada que decir")
    try:
        audio = elevenlabs.synthesize(spoken, req.voice_id)
    except elevenlabs.ElevenLabsError as e:
        raise HTTPException(502, str(e))
    return Response(content=audio, media_type="audio/mpeg")


@app.post("/api/voice/speak")
def voice_speak(req: SpeakRequest):
    """Respuesta completa → resumen hablado breve (LLM rápido) → audio/mpeg.

    La voz dice lo esencial (1-3 frases con las cifras clave); el detalle,
    tablas y gráficos quedan en pantalla.
    """
    spoken = speech.summarize_for_voice(req.text)
    if not spoken:
        raise HTTPException(400, "Nada que decir")
    try:
        audio = elevenlabs.synthesize(spoken)
    except elevenlabs.ElevenLabsError as e:
        raise HTTPException(502, str(e))
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/gaps")
def gaps_page():
    """Tabla manual de huecos (pérdidas de lecturas) — analisis_huecos."""
    return FileResponse(STATIC / "gaps.html")


@app.get("/api/gaps")
def gaps_data(empresa: Optional[str] = None,
              punto: Optional[str] = None,
              fecha: Optional[str] = None,
              min_duracion: float = 0):
    """Lista los huecos registrados en analisis_huecos con filtros."""
    sql = """SELECT fecha, empresa, sede, tablero, punto, point_id,
                    to_char(inicio AT TIME ZONE 'America/Lima','YYYY-MM-DD HH24:MI:SS') AS inicio,
                    to_char(fin AT TIME ZONE 'America/Lima','YYYY-MM-DD HH24:MI:SS') AS fin,
                    duracion_min, lecturas_faltantes
             FROM analisis_huecos WHERE true"""
    params = []
    if empresa:
        sql += " AND empresa ILIKE %s"
        params.append(f"%{empresa}%")
    if punto:
        sql += " AND punto ILIKE %s"
        params.append(f"%{punto}%")
    if fecha:
        sql += " AND fecha = %s"
        params.append(fecha)
    if min_duracion > 0:
        sql += " AND duracion_min >= %s"
        params.append(min_duracion)
    sql += " ORDER BY duracion_min DESC, inicio"
    conn = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT,
                            user=config.DB_USER, password=config.DB_PASSWORD,
                            dbname=config.DB_NAME)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return {"rows": [dict(zip(cols, r)) for r in cur.fetchall()],
                    "total": cur.rowcount if cur.rowcount > 0 else 0}
    finally:
        conn.close()


@app.get("/api/eventos")
def eventos_data(empresa: Optional[str] = None,
                 punto: Optional[str] = None,
                 fecha: Optional[str] = None,
                 min_duracion: float = 0):
    """Eventos agrupados (accidentes) desde analisis_eventos, con filtros."""
    sql = """SELECT fecha, empresa, sede, tablero, punto, point_id,
                    to_char(inicio AT TIME ZONE 'America/Lima','YYYY-MM-DD HH24:MI:SS') AS inicio,
                    to_char(fin AT TIME ZONE 'America/Lima','YYYY-MM-DD HH24:MI:SS') AS fin,
                    minutos_sin_datos, lecturas_faltantes, n_huecos, gap_mayor_min
             FROM analisis_eventos WHERE true"""
    params = []
    if empresa:
        sql += " AND empresa ILIKE %s"
        params.append(f"%{empresa}%")
    if punto:
        sql += " AND punto ILIKE %s"
        params.append(f"%{punto}%")
    if fecha:
        sql += " AND fecha = %s"
        params.append(fecha)
    if min_duracion > 0:
        sql += " AND minutos_sin_datos >= %s"
        params.append(min_duracion)
    sql += " ORDER BY minutos_sin_datos DESC, inicio"
    conn = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT,
                            user=config.DB_USER, password=config.DB_PASSWORD,
                            dbname=config.DB_NAME)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return {"rows": rows, "total": len(rows)}
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  ZEIA web → http://localhost:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
