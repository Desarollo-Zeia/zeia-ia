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
from datetime import date, datetime, timedelta

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
    """Lista los huecos (pérdidas de lecturas) desde el último analisis/*.csv."""
    path = _latest_csv("huecos_*.csv")
    if not path:
        raise HTTPException(404, "Falta huecos_*.csv. Ejecutar la exportación "
                                 "de analisis_huecos.")
    rows = _rows_from_csv(path, empresa, punto, fecha)
    if min_duracion > 0:
        rows = [r for r in rows if float(r.get("duracion_min", 0) or 0) >= min_duracion]
    rows.sort(key=lambda r: (-float(r.get("duracion_min", 0) or 0), r.get("inicio", "")))
    return {"rows": rows, "total": len(rows)}


@app.get("/api/eventos")
def eventos_data(empresa: Optional[str] = None,
                 punto: Optional[str] = None,
                 fecha: Optional[str] = None,
                 min_duracion: float = 0):
    """Eventos agrupados (accidentes) desde el último analisis/eventos_*.csv."""
    path = _latest_csv("eventos_*.csv")
    if not path:
        raise HTTPException(404, "Falta eventos_*.csv. Ejecutar la exportación "
                                 "de analisis_huecos.")
    rows = _rows_from_csv(path, empresa, punto, fecha)
    if min_duracion > 0:
        rows = [r for r in rows if float(r.get("minutos_sin_datos", 0) or 0) >= min_duracion]
    rows.sort(key=lambda r: (-float(r.get("minutos_sin_datos", 0) or 0), r.get("inicio", "")))
    return {"rows": rows, "total": len(rows)}


def _latest_csv(pattern: str) -> Optional[Path]:
    """Devuelve el CSV más nuevo de analisis/ que cumpla el patrón (no ambiental)."""
    if not ANALISIS.exists():
        return None
    files = [p for p in ANALISIS.glob(pattern)
             if "ambiental" not in p.name]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _rows_from_csv(path: Path, empresa: Optional[str], punto: Optional[str],
                   fecha: Optional[str]) -> list[dict]:
    """Lee un CSV de huecos/eventos y aplica los filtros de la query."""
    rows = []
    clean = lambda v: (v or "").strip()
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if empresa and empresa.lower() not in r["empresa"].lower():
                continue
            if punto and punto.lower() not in r["punto"].lower():
                continue
            if fecha and r["fecha"] != fecha:
                continue
            row = dict(r)
            if "inicio (Lima)" in row:
                row["inicio"] = row.pop("inicio (Lima)")
            if "fin (Lima)" in row:
                row["fin"] = row.pop("fin (Lima)")
            row["punto"] = clean(row.get("punto"))
            rows.append(row)
    return rows


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
                                 "python scripts/analisis_huecos_ambiental.py --export")
    return jsonlib.loads(path.read_text(encoding="utf-8"))


# --- Reporte semanal de cobertura (energía + ambiental, para jefes) -----------
# Página limpia: /reporte-semanal (rango default = última semana completa
# lunes-domingo; acepta ?desde=AAAA-MM-DD&hasta=AAAA-MM-DD).

DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves",
           "Viernes", "Sábado", "Domingo"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
ESPERADAS_DIA = 1440  # 1 lectura/minuto
_RANK_ESTADO = {"completo": 0, "parcial": 1, "hueco": 2}


def _etiqueta_dia(d: date) -> str:
    return f"{DIAS_ES[d.weekday()]} {d.day:02d} de {MESES_ES[d.month - 1]}"


def _rango_semanal(desde: Optional[str],
                   hasta: Optional[str]) -> tuple[date, date]:
    if desde and hasta:
        return date.fromisoformat(desde), date.fromisoformat(hasta)
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday() + 7)  # lunes semana pasada
    return lunes, lunes + timedelta(days=6)


def _titulo_rango(d1: date, d2: date) -> str:
    if d1.month == d2.month:
        return (f"{DIAS_ES[d1.weekday()]} {d1.day:02d} al "
                f"{DIAS_ES[d2.weekday()].lower()} {d2.day:02d} de "
                f"{MESES_ES[d2.month - 1]} de {d2.year}")
    return f"{_etiqueta_dia(d1)} al {_etiqueta_dia(d2)} de {d2.year}"


def _compactar_dias(dias: list[date]) -> str:
    """'lun 07 a vie 11' / 'lunes 07 de septiembre' en español."""
    if not dias:
        return ""
    dias = sorted(dias)
    if len(dias) == 1:
        return _etiqueta_dia(dias[0]).lower()
    grupos, ini, prev = [], dias[0], dias[0]
    for d in dias[1:] + [None]:
        if d is None or d != prev + timedelta(days=1):
            grupos.append((ini, prev))
            ini = d
        prev = d if d else prev
    partes = []
    for a, b in grupos:
        if a == b:
            partes.append(_etiqueta_dia(a).lower())
        else:
            partes.append(f"{DIAS_ES[a.weekday()].lower()} {a.day:02d} a "
                          f"{_etiqueta_dia(b).lower()}")
    return ", ".join(partes)


def _reporte_energia(d1: date, d2: date) -> dict:
    s1, s2 = d1.isoformat(), d2.isoformat()
    path = ANALISIS / "cobertura_diaria.csv"
    if not path.exists():
        raise HTTPException(404, "Falta cobertura_diaria.csv. Ejecutar: "
                                 "python scripts/analisis_huecos.py --export")
    dias = []
    d = d1
    while d <= d2:
        dias.append(d.isoformat())
        d += timedelta(days=1)
    por_punto: dict = {}
    tot_lect = 0
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not (s1 <= r["dia"] <= s2):
                continue
            key = (r["empresa"], r["sede"], r["tablero"], r["punto"])
            p = por_punto.setdefault(key, {
                "empresa": r["empresa"], "sede": r["sede"],
                "tablero": r["tablero"], "punto": r["punto"],
                "point_id": r["point_id"], "dias": {}})
            p["dias"][r["dia"]] = {"estado": r["estado"],
                                   "lecturas": int(r["lecturas"] or 0),
                                   "pct": float(r["pct"] or 0)}
            tot_lect += int(r["lecturas"] or 0)
    filas, incidencias = [], []
    n_ok = 0
    for key in sorted(por_punto):
        p = por_punto[key]
        celdas = {}
        for dia in dias:
            c = p["dias"].get(dia)
            if c is None:  # sin fila ese día = hueco total
                c = {"estado": "hueco", "lecturas": 0, "pct": 0.0}
            celdas[dia] = c
        mal = {dia: c for dia, c in celdas.items() if c["estado"] != "completo"}
        con_inc = bool(mal)
        n_hueco = sum(1 for c in mal.values() if c["estado"] == "hueco")
        n_parc = len(mal) - n_hueco
        filas.append({**{k: p[k] for k in ("empresa", "sede", "tablero",
                                          "punto", "point_id")},
                      "dias": celdas, "n_hueco": n_hueco, "n_parcial": n_parc,
                      "con_incidencia": con_inc})
        if not con_inc:
            n_ok += 1
            continue
        det = f"{n_hueco} día(s) sin datos" if n_hueco else ""
        if n_parc:
            det += ("; " if det else "") + f"{n_parc} día(s) parcial(es)"
        incidencias.append({"punto": f"{p['empresa']} {p['sede']} · {p['punto']}",
                            "detalle": det,
                            "orden": (n_hueco, n_parc)})
    filas.sort(key=lambda f: (not f["con_incidencia"], -f["n_hueco"],
                              -f["n_parcial"], f["punto"]))
    # Etiquetas cortas únicas: empresa/sede · punto (+ tablero si colisiona)
    vistas = [f"{f['empresa']}/{f['sede']} · {f['punto']}" for f in filas]
    if len(set(vistas)) < len(vistas):
        vistas = [f"{f['empresa']}/{f['sede']} · {f['punto']} ({f['tablero']})"
                  for f in filas]
    for f, v in zip(filas, vistas):
        f["etiqueta"] = v
    n_puntos = len(por_punto)
    cobertura = (round(100 * tot_lect / (n_puntos * len(dias) * ESPERADAS_DIA), 1)
                 if n_puntos else 0.0)
    # Puntos sin ningún dato en la semana (inventario del resumen)
    sin_datos = []
    try:
        res = jsonlib.loads((ANALISIS / "cobertura_resumen.json")
                            .read_text(encoding="utf-8"))
        for p in res.get("puntos", []):
            if not p.get("con_datos") or (p.get("fin") or "") < s1:
                sin_datos.append(f"{p['empresa']}/{p['sede']} · {p['punto']}")
    except (OSError, ValueError):
        pass
    incidencias.sort(key=lambda i: (-i["orden"][0], -i["orden"][1]))
    return {"cobertura_pct": cobertura, "puntos_total": n_puntos,
            "puntos_ok": n_ok,
            "puntos_incidencias": sum(1 for f in filas if f["con_incidencia"]),
            "dias_hueco": sum(f["n_hueco"] for f in filas),
            "puntos_sin_datos": sorted(set(sin_datos)),
            "filas": [{k: f[k] for k in ("etiqueta", "empresa", "sede",
                                        "tablero", "punto", "dias",
                                        "con_incidencia")}
                      for f in filas],
            "incidencias": [{"punto": i["punto"], "detalle": i["detalle"]}
                            for i in incidencias[:8]]}


def _reporte_ambiental(d1: date, d2: date) -> dict:
    s1, s2 = d1.isoformat(), d2.isoformat()
    p_res = ANALISIS / "huecos_ambiental_salas.json"
    p_cob = ANALISIS / "cobertura_diaria_ambiental_salas.json"
    for p in (p_res, p_cob):
        if not p.exists():
            raise HTTPException(404, f"Falta {p.name}. Ejecutar: "
                                     "python scripts/analisis_huecos_ambiental.py "
                                     "--export")
    resumen = jsonlib.loads(p_res.read_text(encoding="utf-8"))
    cob = jsonlib.loads(p_cob.read_text(encoding="utf-8"))
    meta = {p["combo_id"]: p for p in resumen.get("puntos", [])}
    dias = []
    d = d1
    while d <= d2:
        dias.append(d.isoformat())
        d += timedelta(days=1)
    salas: dict = {}
    for r in cob:
        if not (s1 <= r["dia"] <= s2):
            continue
        m = meta.get(r["combo_id"])
        if not m or not m.get("con_datos"):
            continue
        key = (m["empresa"], m["sede"], m["lugar"])
        s = salas.setdefault(key, {"empresa": m["empresa"], "sede": m["sede"],
                                   "sala": m["lugar"], "dias": {}})
        dd = s["dias"].setdefault(r["dia"], {"peor": "completo", "inds": []})
        dd["inds"].append({"indicador": m["indicador"], "estado": r["estado"],
                           "lecturas": r["lecturas"], "pct": r.get("pct")})
        if _RANK_ESTADO.get(r["estado"], 0) > _RANK_ESTADO[dd["peor"]]:
            dd["peor"] = r["estado"]
    # Resumen por sala (KPIs + incidencias)
    incidencias = []
    n_ok = 0
    for key in sorted(salas):
        s = salas[key]
        celdas = {}
        for dia in dias:
            c = s["dias"].get(dia)
            if c is None:
                c = {"peor": "hueco", "inds": []}
            celdas[dia] = c
        mal = {dia: c for dia, c in celdas.items() if c["peor"] != "completo"}
        if not mal:
            n_ok += 1
            continue
        dias_hueco = sorted(date.fromisoformat(x) for x, c in mal.items()
                            if c["peor"] == "hueco")
        dias_parc = sorted(date.fromisoformat(x) for x, c in mal.items()
                           if c["peor"] == "parcial")
        det = ""
        if dias_hueco:
            det += f"sin datos {_compactar_dias(dias_hueco)}"
        if dias_parc:
            det += ("; " if det else "") + f"parcial {_compactar_dias(dias_parc)}"
        incidencias.append({"punto": s["sala"], "detalle": det,
                            "orden": (len(dias_hueco), len(dias_parc))})
    # Filas del heatmap: una por combo (sala × indicador), como el /gaps
    combos: dict = {}
    for r in cob:
        if not (s1 <= r["dia"] <= s2):
            continue
        m = meta.get(r["combo_id"])
        if not m or not m.get("con_datos"):
            continue
        c = combos.setdefault(r["combo_id"], {
            "empresa": m["empresa"], "sede": m["sede"], "sala": m["lugar"],
            "indicador": m["indicador"], "dias": {}})
        c["dias"][r["dia"]] = {"estado": r["estado"],
                               "lecturas": r["lecturas"], "pct": r.get("pct")}
    filas = []
    for cid in sorted(combos):
        c = combos[cid]
        celdas = {}
        for dia in dias:
            cc = c["dias"].get(dia)
            if cc is None:
                cc = {"estado": "hueco", "lecturas": 0, "pct": 0}
            celdas[dia] = cc
        con_inc = any(cc["estado"] != "completo" for cc in celdas.values())
        filas.append({"empresa": c["empresa"], "sede": c["sede"],
                      "sala": c["sala"], "indicador": c["indicador"],
                      "dias": celdas,
                      "n_hueco": sum(1 for cc in celdas.values()
                                     if cc["estado"] == "hueco"),
                      "con_incidencia": con_inc})
    filas.sort(key=lambda f: (not f["con_incidencia"], -f["n_hueco"],
                              f["sala"], f["indicador"]))
    misma_sede = len({(f["empresa"], f["sede"]) for f in filas}) <= 1
    for f in filas:
        base = f["sala"] if misma_sede else f"{f['sede']} · {f['sala']}"
        f["etiqueta"] = f"{base} · {f['indicador']}"
    n_salas = len(salas)
    tot_sd = n_salas * len(dias)
    ok_sd = 0
    for s in salas.values():
        for dia in dias:
            c = s["dias"].get(dia)
            if c and c["inds"] and all(i["estado"] == "completo"
                                      for i in c["inds"]):
                ok_sd += 1
    cobertura = round(100 * ok_sd / tot_sd, 1) if tot_sd else 100.0
    incidencias.sort(key=lambda i: (-i["orden"][0], -i["orden"][1]))
    n_comb_ok = sum(1 for f in filas if not f["con_incidencia"])
    return {"cobertura_pct": cobertura, "salas_total": n_salas,
            "salas_ok": n_ok,
            "salas_incidencias": n_salas - n_ok,
            "combos_total": len(filas), "combos_ok": n_comb_ok,
            "filas": [{k: f[k] for k in ("etiqueta", "empresa", "sede",
                                        "sala", "indicador", "dias",
                                        "con_incidencia")}
                      for f in filas],
            "incidencias": [{"punto": i["punto"], "detalle": i["detalle"]}
                            for i in incidencias[:8]]}


@app.get("/api/reporte-semanal")
def reporte_semanal_data(desde: Optional[str] = None,
                         hasta: Optional[str] = None):
    """Cobertura energía + ambiental de una semana, resumida para jefes."""
    d1, d2 = _rango_semanal(desde, hasta)
    dias = []
    d = d1
    while d <= d2:
        dias.append({"iso": d.isoformat(), "etiqueta": _etiqueta_dia(d),
                     "corta": f"{DIAS_ES[d.weekday()][:3]} {d.day:02d}"})
        d += timedelta(days=1)
    ahora = datetime.now()
    generado = (f"{DIAS_ES[ahora.weekday()]} {ahora.day:02d} de "
                f"{MESES_ES[ahora.month - 1]} de {ahora.year}, "
                f"{ahora.hour:02d}:{ahora.minute:02d}")
    return {"desde": d1.isoformat(), "hasta": d2.isoformat(),
            "titulo_rango": _titulo_rango(d1, d2),
            "generado": generado,
            "dias": dias,
            "energia": _reporte_energia(d1, d2),
            "ambiental": _reporte_ambiental(d1, d2)}


@app.get("/reporte-semanal")
def reporte_semanal_page():
    """Reporte semanal de cobertura (vista limpia para jefes)."""
    return FileResponse(STATIC / "reporte_semanal.html")


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
    host = os.getenv("HOST", "0.0.0.0")
    print(f"\n  ZEIA web → http://localhost:{port} (también http://<tu-ip-local>:{port})\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
