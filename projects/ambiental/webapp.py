#!/usr/bin/env python
"""App web del agente AMBIENTAL: FastAPI + UI de chat con gráficos + huecos.

Proyecto mono-base: consulta SIEMPRE la base `valhalladb` (monitoreo ambiental
de interiores: CO2, temperatura, humedad de salas).

Endpoints de análisis de huecos (mismas vistas que energía):
    /gaps   → dashboard completo (Cobertura diaria, Lectura a lectura, Episodios)
    /reporte → reporte HTML simple (tablas)

Fuentes:
    analisis/huecos_ambiental_{salas,puntos}.json          resumen por combo
    analisis/cobertura_diaria_ambiental_{salas,puntos}.json estado día×combo
    valhalladb readings_reading / readings_readingambiental  lecturas en vivo

Generar los archivos con:
    python scripts/analisis_huecos_ambiental.py --export

Uso:
    python webapp.py           → http://localhost:8001
    PORT=8002 python webapp.py
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

import psycopg2
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

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

app = FastAPI(title="ZEIA - Agente ambiental")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_sessions: dict = {}


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    persona: Optional[str] = None   # "analista" | "gerente"


class PresetRequest(BaseModel):
    persona: Optional[str] = None   # "analista" | "gerente"


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
              persona: Optional[str] = None) -> EnergyAgent:
    """Devuelve el agente de la sesión (separado por perfil)."""
    p = persona or "analista"
    agent = _sessions.get(session_id)
    if agent is None or agent.persona != p:
        # Sesión nueva, o cambió el perfil: crear agente
        agent = EnergyAgent(model=model or config.DEFAULT_MODEL, persona=p)
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
            "personas": [
                {"id": "analista", "label": "Analista técnico",
                 "desc": "Detalle, SQL y análisis minucioso de la data"},
                {"id": "gerente", "label": "Gerente",
                 "desc": "Visión general, guiado con opciones"},
            ]}


@app.post("/api/chat")
def chat(req: ChatRequest):
    session_id = req.session_id or uuid.uuid4().hex
    if not req.message.strip():
        raise HTTPException(400, "Mensaje vacío")
    valid_models = {m["id"] for m in MODEL_OPTIONS}
    model = req.model if req.model in valid_models else None
    agent = get_agent(session_id, model, persona=req.persona)
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


# Preguntas predefinidas para el dashboard inicial, por perfil.
PRESETS = {
    "gerente": [
        "¿Cuál es el estado general de las salas hoy? Temperatura, humedad y CO2 promedio.",
        "¿Qué salas tuvieron CO2 por encima de 1000 ppm este mes?",
        "¿Hubo cortes de monitoreo en agosto? Dime qué salas y cuánto tiempo perdieron.",
        "¿Cómo ha variado la temperatura de las Salas de Operaciones esta semana?",
    ],
    "analista": [
        "Perfil horario de CO2 en la Zona Roja de ayer, cada 3 horas.",
        "¿Qué salas tuvieron cortes de monitoreo en agosto? Detalla duración y puntos perdidos por sala.",
        "Compara temperatura y humedad de todas las salas esta semana (promedio, min, max).",
        "¿Hay indicadores por encima de umbrales (CO2>1200, TEMP>27, HUM>70) en el mes?",
    ],
}


@app.post("/api/dashboard/preset")
def dashboard_preset(req: PresetRequest):
    """Ejecuta las preguntas predefinidas del perfil y devuelve las
    cards del dashboard inicial con sus respuestas y gráficos."""
    cfg = config.get_db_config()
    persona = req.persona if req.persona in ("analista", "gerente") else "analista"
    preguntas = PRESETS.get(persona)

    cards = []
    for i, pregunta in enumerate(preguntas):
        agent = EnergyAgent(persona=persona)
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


# --- Huecos ambientales ------------------------------------------------------

MAX_COMBOS = 40   # "comparar todos"
MAX_DIAS = 10


def _json(path: Path) -> dict | list:
    if not path.exists():
        raise HTTPException(404, f"Falta {path.name}. Ejecutar: "
                                 "python scripts/analisis_huecos_ambiental.py --export")
    return jsonlib.loads(path.read_text(encoding="utf-8"))


def _resumen(track: str) -> dict:
    if track not in ("salas", "puntos"):
        raise HTTPException(400, "track inválido (salas|puntos)")
    return _json(ANALISIS / f"huecos_ambiental_{track}.json")


def _db():
    return psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT,
                            user=config.DB_USER, password=config.DB_PASSWORD,
                            dbname=config.DB_NAME)


@app.get("/gaps")
def gaps_page():
    """Dashboard completo de huecos ambientales."""
    return FileResponse(STATIC / "ambiental_gaps.html")


@app.get("/reporte", response_class=HTMLResponse)
def reporte_simple():
    """Reporte HTML simple del análisis de huecos ambiental."""
    try:
        data = _resumen("puntos") or {}
    except HTTPException:
        return HTMLResponse("<h1>Ambiental</h1><p>Falta el análisis. "
                            "Ejecutar <code>python scripts/analisis_huecos_ambiental.py --export</code>"
                            "</p>", status_code=404)

    meta = data
    puntos = data["puntos"]
    activos = [p for p in puntos if p.get("con_datos")
               and p.get("staleness_dias", 999) <= 2]
    caidos = [p for p in puntos if p.get("con_datos")
              and p.get("staleness_dias", 0) > 2]
    sin_datos = [p for p in puntos if not p.get("con_datos")]

    filas = "".join(
        f"<tr><td>{p.get('empresa','?')}</td><td>{p.get('sede','?')}</td>"
        f"<td>{p.get('lugar', p.get('punto','?'))}</td>"
        f"<td>{p.get('indicador','?')}</td><td>{p.get('sensor','?')}</td>"
        f"<td>{p.get('inicio','-')}</td><td>{p.get('fin','-')}</td>"
        f"<td>{p.get('dias_datos','-')}</td><td>{p.get('dias_completos','-')}</td>"
        f"<td>{p.get('dias_parciales','-')}</td><td>{p.get('dias_hueco','-')}</td>"
        f"<td style='color:{'#16a34a' if p.get('staleness_dias',999)<=2 else '#dc2626'}'>"
        f"{p.get('staleness_dias','-')}</td></tr>\n"
        for p in puntos)

    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>ZEIA Ambiental — Huecos</title>
<style>body{{font-family:system-ui,sans-serif;margin:20px}}
table{{border-collapse:collapse;font-size:13px}}
td,th{{border:1px solid #ddd;padding:4px 8px}}
th{{background:#f3f4f6}} .cards{{display:flex;gap:16px;margin-bottom:16px}}
.card{{border:1px solid #ddd;border-radius:8px;padding:12px 16px}}
.card b{{font-size:24px}} .red{{color:#dc2626}} .green{{color:#16a34a}}</style>
</head><body>
<h1>ZEIA — Análisis de huecos ambiental</h1>
<p>Generado: {meta['generado']} · Datos máximos: {meta['global_max_day']} ·
<a href="/gaps">Ir al dashboard completo →</a></p><div class="cards">
<div class="card"><b>{meta['combos_inventario']}</b><br>combos en inventario</div>
<div class="card"><b class="green">{len(activos)}</b><br>al día (≤2 días)</div>
<div class="card"><b class="red">{len(caidos)}</b><br>caídos (&gt;2 días)</div>
<div class="card"><b class="red">{len(sin_datos)}</b><br>sin lecturas</div>
</div>
<h2>Combos por sala/indicador</h2>
<table><tr><th>Empresa</th><th>Sede</th><th>Sala</th><th>Indicador</th><th>Sensor</th>
<th>Inicio</th><th>Fin</th><th>Días</th><th>Comp.</th><th>Parc.</th><th>Hueco</th>
<th>Staleness</th></tr>
{filas}</table>
</body></html>"""
    return HTMLResponse(html)


# --- Cobertura diaria (archivos generados por el pipeline) -------------------

@app.get("/api/ambiental/cobertura/resumen")
def cobertura_resumen(track: str = Query("salas")):
    """Resumen por combo + episodios + métricas globales."""
    return _resumen(track)


@app.get("/api/ambiental/huecos/salas")
def huecos_salas_compat():
    """Compatibilidad: resumen de salas."""
    return _resumen("salas")


@app.get("/api/ambiental/huecos/puntos")
def huecos_puntos_compat():
    """Compatibilidad: resumen de puntos ambientales."""
    return _resumen("puntos")


@app.get("/api/ambiental/cobertura/diaria")
def cobertura_diaria(track: str = Query("salas"),
                     empresa: str | None = None,
                     lugar: str | None = None,
                     desde: str | None = None,
                     hasta: str | None = None):
    """Estado de cada día por combo (completo/parcial/hueco)."""
    if track not in ("salas", "puntos"):
        raise HTTPException(400, "track inválido (salas|puntos)")
    resumen = _resumen(track)
    meta_combo = {p["combo_id"]: p for p in resumen["puntos"]}
    rows = _json(ANALISIS / f"cobertura_diaria_ambiental_{track}.json")

    def ok(c: dict) -> bool:
        m = meta_combo.get(c["combo_id"], {})
        if empresa and empresa.lower() not in (m.get("empresa") or "").lower():
            return False
        if lugar and lugar.lower() not in ((m.get("lugar") or "") + " " +
                                           (m.get("indicador") or "")).lower():
            return False
        if desde and c["dia"] < desde:
            return False
        if hasta and c["dia"] > hasta:
            return False
        return True

    out = [c for c in rows if ok(c)]
    return {"rows": out, "total": len(out)}


# --- Lectura a lectura (en vivo contra valhalladb) ---------------------------

@app.get("/api/ambiental/lecturas")
def lecturas_data(track: str = Query("salas"),
                  combos: str | None = None,
                  desde: str | None = None,
                  hasta: str | None = None):
    """Minutos (hora Lima) con lecturas por combo, para el heatmap por minuto.

    track=salas  → readings_reading (indicator_device_id)
    track=puntos → readings_readingambiental (indicator_device_id)
    Solo devuelve minutos CON lectura; el navegador infiere los faltantes.
    """
    ids = sorted({int(x) for x in (combos or "").split(",") if x.strip().isdigit()})
    if not ids:
        raise HTTPException(400, "Parámetro combos requerido, ej: ?combos=317,318")
    if len(ids) > MAX_COMBOS:
        raise HTTPException(400, f"Máximo {MAX_COMBOS} combos a la vez")
    if not desde:
        desde = (date.today() - timedelta(days=1)).isoformat()
    hasta = hasta or desde
    try:
        d0 = date.fromisoformat(desde)
        d1 = date.fromisoformat(hasta)
    except ValueError:
        raise HTTPException(400, "Fechas inválidas (usar YYYY-MM-DD)")
    if d1 < d0 or (d1 - d0).days >= MAX_DIAS:
        raise HTTPException(400, f"Rango máximo {MAX_DIAS} días")

    tabla = ("readings_reading" if track == "salas"
             else "readings_readingambiental")
    placeholders = ",".join(["%s"] * len(ids))
    sql = f"""
        SELECT r.indicator_device_id AS combo_id,
               (r.created_at AT TIME ZONE 'America/Lima')::date AS dia,
               EXTRACT(HOUR   FROM r.created_at AT TIME ZONE 'America/Lima')::int AS hora,
               EXTRACT(MINUTE FROM r.created_at AT TIME ZONE 'America/Lima')::int AS minuto,
               count(*) AS n
        FROM {tabla} r
        WHERE r.indicator_device_id IN ({placeholders})
          AND r.created_at >= (CAST(%s AS date))::timestamp AT TIME ZONE 'America/Lima'
          AND r.created_at <  (CAST(%s AS date) + 1)::timestamp AT TIME ZONE 'America/Lima'
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
    """
    params = [*ids, d0.isoformat(), d1.isoformat()]
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            # Horizonte de datos por combo: la última lectura delimita lo evaluable.
            meta = []
            for cid in ids:
                regs = [r for r in rows if r["combo_id"] == cid]
                if not regs:
                    meta.append({"combo_id": cid, "ultimo_dia": None,
                                 "ultimo_min_idx": None, "ultima_hhmm": None})
                    continue
                ult = regs[-1]
                meta.append({
                    "combo_id": cid,
                    "ultimo_dia": ult["dia"],
                    "ultimo_min_idx": ult["hora"] * 60 + ult["minuto"],
                    "ultima_hhmm": f"{int(ult['hora']):02d}:{int(ult['minuto']):02d}",
                })
            return {"rows": rows, "total": len(rows), "meta": {"combos": meta}}
    finally:
        conn.close()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"\n  ZEIA ambiental (chat + huecos) → http://localhost:{port}"
          f" (también http://<tu-ip-local>:{port})\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
