#!/usr/bin/env python
"""App web del agente de energía: FastAPI + UI de chat con gráficos.

Uso:
    python webapp.py           → http://localhost:8000
    PORT=8080 python webapp.py
"""
from __future__ import annotations

import csv
import json as jsonlib
import os
import sys
import uuid
from datetime import date, timedelta

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
ANALISIS = ROOT / "analisis"

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
    base: Optional[str] = None      # "energia" | "ambiental"
    persona: Optional[str] = None   # "analista" | "gerente"


class PresetRequest(BaseModel):
    base: str                        # "energia" | "ambiental"
    persona: Optional[str] = None    # "analista" | "gerente"


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


def get_agent(session_id: str, model: Optional[str],
              base: Optional[str] = None,
              persona: Optional[str] = None) -> EnergyAgent:
    """Devuelve el agente de la sesión (separado por base y persona)."""
    cfg = config.get_db_config(base or config.DEFAULT_BASE)
    p = persona or "analista"
    agent = _sessions.get(session_id)
    if agent is None or agent.base != cfg.name or agent.persona != p:
        # Sesión nueva, o cambió módulo/perfil: crear agente
        agent = EnergyAgent(model=model or config.DEFAULT_MODEL, base=cfg.name,
                            persona=p)
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
    return {"default": config.DEFAULT_MODEL, "options": MODEL_OPTIONS,
            "bases": [{"id": c.name, "label": c.label, "dbname": c.dbname}
                      for c in (config.ENERGIA_DB, config.AMBIENTAL_DB)],
            "personas": [
                {"id": "analista", "label": "Analista técnico",
                 "desc": "Detalle, SQL y análisis minucioso de la data"},
                {"id": "gerente", "label": "Gerente",
                 "desc": "Visión general, plata (S/), guiado con opciones"},
            ]}


@app.post("/api/chat")
def chat(req: ChatRequest):
    session_id = req.session_id or uuid.uuid4().hex
    if not req.message.strip():
        raise HTTPException(400, "Mensaje vacío")
    valid_models = {m["id"] for m in MODEL_OPTIONS}
    model = req.model if req.model in valid_models else None
    agent = get_agent(session_id, model, base=req.base, persona=req.persona)
    result = agent.ask(req.message)
    charts = [c for c in (sanitize_chart(s) for s in result.charts) if c]
    return {
        "session_id": session_id,
        "answer": result.answer,
        "charts": charts,
        "queries": result.queries,
        "usage": result.usage,
        "error": result.error,
        "base": agent.base,
        "persona": agent.persona,
    }


# Preguntas predefinidas para el dashboard inicial, por módulo y perfil.
PRESETS = {
    ("energia", "gerente"): [
        "¿Cuál es el consumo total de energía de este mes y cuánto costaría en soles?",
        "¿Cuánto cuesta cada sede este mes? Ordena de mayor a menor costo.",
        "¿Hay alertas críticas activas esta semana? Dime en qué sede y de qué tipo.",
        "Muéstrame la curva de consumo promedio por hora del día de ayer.",
    ],
    ("energia", "analista"): [
        "Detalla el consumo por punto de medición de la sede más consumidora este mes.",
        "¿Cuál es la demanda máxima (kW) y su P95 de las sedes con datos esta semana?",
        "¿Qué tableros tienen huecos de lecturas en los últimos 7 días?",
        "¿Cómo se descompone el consumo entre hora punta y fuera de punta este mes?",
    ],
    ("ambiental", "gerente"): [
        "¿Cuál es el estado general de las salas hoy? Temperatura, humedad y CO2 promedio.",
        "¿Qué salas tuvieron CO2 por encima de 1000 ppm este mes?",
        "¿Hubo cortes de monitoreo en agosto? Dime qué salas y cuánto tiempo perdieron.",
        "¿Cómo ha variado la temperatura de las Salas de Operaciones esta semana?",
    ],
    ("ambiental", "analista"): [
        "Perfil horario de CO2 en la Zona Roja de ayer, cada 3 horas.",
        "¿Qué salas tuvieron cortes de monitoreo en agosto? Detalla duración y puntos perdidos por sala.",
        "Compara temperatura y humedad de todas las salas esta semana (promedio, min, max).",
        "¿Hay indicadores por encima de umbrales (CO2>1200, TEMP>27, HUM>70) en el mes?",
    ],
}


@app.post("/api/dashboard/preset")
def dashboard_preset(req: PresetRequest):
    """Ejecuta las preguntas predefinidas de (base, persona) y devuelve las
    cards del dashboard inicial con sus respuestas y gráficos."""
    cfg = config.get_db_config(req.base)
    persona = req.persona if req.persona in ("analista", "gerente") else "analista"
    preguntas = PRESETS.get((cfg.name, persona))
    if not preguntas:
        raise HTTPException(404, f"Sin preset para {cfg.name}/{persona}")

    cards = []
    for i, pregunta in enumerate(preguntas):
        agent = EnergyAgent(base=cfg.name, persona=persona)
        result = agent.ask(pregunta)
        charts = [c for c in (sanitize_chart(s) for s in result.charts) if c]
        cards.append({
            "id": uuid.uuid4().hex[:8],
            "prompt": pregunta,
            "answer": result.answer,
            "charts": charts,
            "error": result.error,
        })
    return {"base": cfg.name, "persona": persona, "cards": cards}


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


# --- Cobertura diaria (informe de huecos a nivel de días) ---------------------
# Datos generados por: python scripts/analisis_huecos.py --export
# Archivos: analisis/cobertura_diaria.csv + analisis/cobertura_resumen.json


@app.get("/api/cobertura")
def cobertura_data(empresa: Optional[str] = None,
                   punto: Optional[str] = None,
                   desde: Optional[str] = None,
                   hasta: Optional[str] = None):
    """Cobertura diaria por punto (estado de cada día)."""
    path = ANALISIS / "cobertura_diaria.csv"
    if not path.exists():
        raise HTTPException(404, "Falta cobertura_diaria.csv. Ejecutar: "
                                 "python scripts/analisis_huecos.py --export")
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if empresa and empresa.lower() not in r["empresa"].lower():
                continue
            if punto and punto.lower() not in r["punto"].lower():
                continue
            if desde and r["dia"] < desde:
                continue
            if hasta and r["dia"] > hasta:
                continue
            rows.append(r)
    return {"rows": rows, "total": len(rows)}


@app.get("/api/cobertura/resumen")
def cobertura_resumen():
    """Resumen por punto + episodios + métricas globales."""
    path = ANALISIS / "cobertura_resumen.json"
    if not path.exists():
        raise HTTPException(404, "Falta cobertura_resumen.json. Ejecutar: "
                                 "python scripts/analisis_huecos.py --export")
    return jsonlib.loads(path.read_text(encoding="utf-8"))


# --- Lectura a lectura (presencia por minuto) ---------------------------------
# Vista "minuto a minuto": para 1-3 puntos y un rango de pocos días devuelve
# los minutos (hora Lima) con lecturas. El resto lo completa el navegador
# como minutos faltantes (rojo en el heatmap).

MAX_LECTURAS_PUNTOS = 40   # "comparar todos": cubre todos los puntos con datos
MAX_LECTURAS_DIAS = 10


def _parse_puntos(puntos: Optional[str]) -> list[int]:
    ids = set()
    for part in (puntos or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return sorted(ids)


@app.get("/api/lecturas")
def lecturas_data(puntos: Optional[str] = None,
                  desde: Optional[str] = None,
                  hasta: Optional[str] = None):
    """Minutos (hora Lima) con lecturas por punto, para el heatmap por minuto.

    Límites: MAX_LECTURAS_PUNTOS puntos y MAX_LECTURAS_DIAS días.
    Solo devuelve minutos CON lectura; el navegador infiere los faltantes.
    """
    ids = _parse_puntos(puntos)
    if not ids:
        raise HTTPException(400, "Parámetro puntos requerido, ej: ?puntos=76,75")
    if len(ids) > MAX_LECTURAS_PUNTOS:
        raise HTTPException(400, f"Máximo {MAX_LECTURAS_PUNTOS} puntos a la vez")
    if not desde:
        desde = (date.today() - timedelta(days=1)).isoformat()
    hasta = hasta or desde
    try:
        d0 = date.fromisoformat(desde)
        d1 = date.fromisoformat(hasta)
    except ValueError:
        raise HTTPException(400, "Fechas inválidas (usar YYYY-MM-DD)")
    if d1 < d0 or (d1 - d0).days >= MAX_LECTURAS_DIAS:
        raise HTTPException(400, f"Rango máximo {MAX_LECTURAS_DIAS} días")

    placeholders = ",".join(["%s"] * len(ids))
    sql = f"""
        SELECT r.measurement_point_id AS point_id,
               (r.created_at AT TIME ZONE 'America/Lima')::date AS dia,
               EXTRACT(HOUR   FROM r.created_at AT TIME ZONE 'America/Lima')::int AS hora,
               EXTRACT(MINUTE FROM r.created_at AT TIME ZONE 'America/Lima')::int AS minuto,
               count(*) AS n
        FROM readings_reading r
        WHERE r.measurement_point_id IN ({placeholders})
          AND r."EPpos_value" IS NOT NULL
          AND r.created_at >= (CAST(%s AS date))::timestamp AT TIME ZONE 'America/Lima'
          AND r.created_at <  (CAST(%s AS date) + 1)::timestamp AT TIME ZONE 'America/Lima'
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
    """
    params = [*ids, d0.isoformat(), d1.isoformat()]
    conn = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT,
                            user=config.DB_USER, password=config.DB_PASSWORD,
                            dbname=config.DB_NAME)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            # Horizonte de datos por punto: la copia local puede terminar en
            # medio de un día (sync pendiente). La última lectura de cada punto
            # delimita los minutos evaluables; el resto NO es un hueco.
            meta = {"puntos": []}
            for pid in ids:
                regs = [r for r in rows if r["point_id"] == pid]
                if not regs:
                    meta["puntos"].append({"point_id": pid, "ultimo_dia": None,
                                           "ultimo_min_idx": None, "ultima_hhmm": None})
                    continue
                ult = regs[-1]  # ORDER BY point, dia, hora, minuto
                meta["puntos"].append({
                    "point_id": pid,
                    "ultimo_dia": ult["dia"],
                    "ultimo_min_idx": ult["hora"] * 60 + ult["minuto"],
                    "ultima_hhmm": f"{int(ult['hora']):02d}:{int(ult['minuto']):02d}",
                })
            return {"rows": rows, "total": len(rows), "meta": meta}
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  ZEIA web → http://localhost:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
