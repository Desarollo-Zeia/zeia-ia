#!/usr/bin/env python
"""Servidor web del análisis AMBIENTAL (valhalladb) — puerto separado de energía.

Dashboard de huecos (mismas vistas que el de energía) + reporte simple:

    /gaps   → dashboard completo (Cobertura diaria, Lectura a lectura, Episodios)
    /       → reporte HTML simple (tablas)

Fuentes:
    analisis/huecos_ambiental_{salas,puntos}.json          resumen por combo
    analisis/cobertura_diaria_ambiental_{salas,puntos}.json estado día×combo
    valhalladb readings_reading / readings_readingambiental  lecturas en vivo

Generar los archivos con:
    python scripts/analisis_huecos_ambiental.py --export

Uso:
    python webapp_ambiental.py      → http://localhost:8001
    PORT=8002 python webapp_ambiental.py
"""
from __future__ import annotations

import csv
import json as jsonlib
import os
import sys
from datetime import date, timedelta

# En Windows la consola usa cp1252; forzar UTF-8.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

import psycopg2
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
ANALISIS = ROOT / "analisis"
STATIC = ROOT / "web" / "static"

DB_HOST = os.getenv("AMBIENTAL_DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("AMBIENTAL_DB_PORT", "5433"))
DB_USER = os.getenv("AMBIENTAL_DB_USER", "postgres")
DB_PASSWORD = os.getenv("AMBIENTAL_DB_PASSWORD", "")
DB_NAME = os.getenv("AMBIENTAL_DB_NAME", "valhalladb")

MAX_COMBOS = 40   # "comparar todos"
MAX_DIAS = 10

app = FastAPI(title="ZEIA - Análisis ambiental (huecos)")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


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
    return psycopg2.connect(host=DB_HOST, port=DB_PORT,
                            user=DB_USER, password=DB_PASSWORD,
                            dbname=DB_NAME)


# --- Dashboard ---------------------------------------------------------------

@app.get("/gaps")
def gaps_page():
    """Dashboard completo de huecos ambientales."""
    return FileResponse(STATIC / "ambiental_gaps.html")


@app.get("/")
def index():
    return RedirectResponse("/gaps")


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
    print(f"\n  ZEIA ambiental (huecos) → http://localhost:{port}/gaps\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
