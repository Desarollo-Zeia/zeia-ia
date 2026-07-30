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
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

from src import config
from src.agent import EnergyAgent

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web" / "static"

# Modelos ofrecidos en el selector (resultado de la evaluación comparativa)
MODEL_OPTIONS = [
    {"id": "qwen/qwen3-coder", "label": "Qwen 3 Coder (recomendado)"},
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


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  ZEIA web → http://localhost:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
