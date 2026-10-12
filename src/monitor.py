"""Monitor de salud de datos en tiempo real (multi-base).

Responde una sola pregunta: **¿qué punto/combo dejó de traer información?**
pensado para escalar a cientos de tiendas.

Claves de diseño:
- El estado en vivo NO escanea la tabla de lecturas completa: en una sola
  consulta acotada a la ventana del día (o 24 h si la fuente está congelada)
  obtiene `max(created_at)` y los minutos con lectura por punto. Con el índice
  `(measurement_point_id, created_at)` esto resuelve en décimas de segundo.
- Los puntos inactivos (no monitoreados) NO cuentan en la disponibilidad; solo
  cuentan los que deberían estar reportando.
- Si TODA la base no tiene datos recientes (`CONGELADA_MIN`), se marca
  `fuente.congelada` y la antigüedad se mide contra el horizonte (la copia),
  para no pintar todo rojo cuando en realidad es un restore/backup.
- Caché en proceso de `CACHE_TTL` segundos: muchos navegadores con auto-refresh
  comparten el mismo cálculo.

Bases:
- `energia`   → puntos  (`enterprises_measurementpoint`)
- `ambiental` → combos  (`equipments_indicatordevice` = indicador × sala)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from . import config
from .db import get_engine

# Lima no tiene horario de verano: offset fijo UTC-5.
LIMA = timezone(timedelta(hours=-5))

# Parámetros por base.
#   cadencia_min: cada cuántos minutos reporta normalmente un punto/combo.
#   ok_min/warn_min: umbrales del semáforo según antigüedad de la última lectura.
#   lookback_h: ventana usada cuando la fuente está congelada.
PARAMS = {
    "energia":   {"cadencia_min": 1, "ok_min": 5,  "warn_min": 15,
                  "lookback_h": 24, "degradado_pct": 70},
    "ambiental": {"cadencia_min": 5, "ok_min": 12, "warn_min": 30,
                  "lookback_h": 24, "degradado_pct": 70},
}

CONGELADA_MIN = 30   # sin datos globales > 30 min => fuente congelada (copia/backup)
CACHE_TTL = 20       # segundos de caché del estado
RITMO_DIAS = 14      # días de historia para estimar la cadencia real de cada combo
RITMO_TTL = 1800     # la cadencia cambia poco: cachear 30 min

# Empresas de prueba que no se monitorean (no son clientes reales).
EXCLUIR = {"testcorp_email", "zeia demo", "zeia", "zeia prueba", "monitoreo casa",
           "machu picchu food"}

_cache: dict = {}
_ritmo_cache: dict = {}


# ---------- Utilidades de consulta --------------------------------------------

def _scalar(base: str, sql: str):
    with get_engine(base).connect() as conn:
        row = conn.execute(text(sql)).fetchone()
    return row[0] if row else None


def _rows(base: str, sql: str, params: Optional[dict] = None):
    with get_engine(base).connect() as conn:
        return conn.execute(text(sql), params or {}).fetchall()


def _ref(maxg) -> tuple:
    """Momento de referencia y si la fuente está congelada.

    Si la base entera no tiene datos recientes, medimos contra el horizonte
    (la última lectura global) en vez de `now`, para no marcar todo caído.
    """
    ahora = datetime.now(timezone.utc)
    congelada = maxg is not None and (ahora - maxg) > timedelta(minutes=CONGELADA_MIN)
    return (maxg if congelada else ahora), congelada


def _ventana(ref: datetime, congelada: bool, p: dict) -> tuple:
    """Ventana de evaluación y minutos esperados en ella.

    En vivo: desde las 00:00 Lima hasta `ref` (pérdida del día en curso).
    Congelada: las últimas `lookback_h` horas hasta el horizonte.
    """
    if congelada:
        ini = ref - timedelta(hours=p["lookback_h"])
        esperado = max(1, round(p["lookback_h"] * 60 / p["cadencia_min"]))
    else:
        rl = ref.astimezone(LIMA)
        ini = datetime(rl.year, rl.month, rl.day, tzinfo=LIMA)
        transcurridos = max(1.0, (ref - ini).total_seconds() / 60)
        esperado = max(1, round(transcurridos / p["cadencia_min"]))
    return ini, ref + timedelta(minutes=1), esperado


# ---------- Inventarios y ventana ---------------------------------------------

_INV_ENERGIA = """
    SELECT e.name AS empresa, eh.name AS sede, ep.name AS grupo,
           d.name AS dispositivo, mp.id AS uid, mp.name AS nombre,
           mp.is_main AS is_main, mp.is_active AS activo
    FROM enterprises_measurementpoint mp
    JOIN devices_device d ON d.id = mp.device_id
    JOIN enterprises_electricalpanel ep ON ep.id = d.electrical_panel_id
    JOIN enterprises_energyheadquarter eh ON eh.id = ep.energy_headquarter_id
    JOIN enterprises_enterprise e ON e.id = eh.enterprise_id
    ORDER BY e.name, eh.name, ep.name, mp.name
"""

_VENT_ENERGIA = """
    SELECT measurement_point_id AS uid,
           max(created_at) AS ult,
           count(DISTINCT date_trunc('minute', created_at)) AS n
    FROM readings_reading
    WHERE created_at >= :ini AND created_at < :fin
      AND "EPpos_value" IS NOT NULL
    GROUP BY 1
"""

_INV_AMBIENTAL = """
    SELECT f.name AS empresa, hq.name AS sede, r.name AS grupo,
           d.type_sensor AS dispositivo, rd.id AS uid, i.name AS nombre,
           true AS activo
    FROM equipments_indicatordevice rd
    JOIN equipments_indicator i ON i.id = rd.indicator_id
    JOIN equipments_device d ON d.id = rd.device_id
    JOIN enterprise_room r ON r.id = d.room_id
    JOIN enterprise_headquarters hq ON hq.id = r.headquarter_id
    JOIN enterprise_enterprise f ON f.id = hq.enterprise_id
    WHERE rd.is_activated
    ORDER BY f.name, hq.name, r.name, i.name
"""

_VENT_AMBIENTAL = """
    SELECT indicator_device_id AS uid,
           max(created_at) AS ult,
           count(*) AS n
    FROM readings_reading
    WHERE created_at >= :ini AND created_at < :fin
    GROUP BY 1
"""

# Cadencia real por combo: lecturas/día en los últimos RITMO_DIAS días.
_RITMO_AMBIENTAL = """
    SELECT indicator_device_id AS uid, count(*) AS n
    FROM readings_reading
    WHERE created_at >= :desde AND created_at < :fin
    GROUP BY 1
"""


def _ritmo_ambiental(ref: datetime) -> dict:
    """Lecturas/día por combo (mediana histórica ≈ ritmo real del sensor)."""
    now = time.time()
    hit = _ritmo_cache.get("ambiental")
    if hit and now - hit[0] < RITMO_TTL:
        return hit[1]
    desde = ref - timedelta(days=RITMO_DIAS)
    rows = _rows("ambiental", _RITMO_AMBIENTAL,
                 {"desde": desde, "fin": ref + timedelta(minutes=1)})
    m = {r[0]: max(0.1, float(r[1]) / RITMO_DIAS) for r in rows}
    _ritmo_cache["ambiental"] = (now, m)
    return m


# ---------- Construcción del estado -------------------------------------------

def _build(base: str, maxg, ref: datetime, congelada: bool,
           inv, vent: dict, esperado: int,
           ritmo_map: Optional[dict] = None,
           window_days: float = 1.0) -> dict:
    p = PARAMS[base]
    cfg = config.get_db_config(base)
    unidades = []
    for r in inv:
        m = r._mapping
        empresa = m["empresa"]
        if (empresa or "").strip().lower() in EXCLUIR:
            continue
        uid = m["uid"]
        activo = bool(m["activo"])
        # Esperado y cadencia por unidad: en ambiental varía por sensor.
        if base == "ambiental":
            ritmo = (ritmo_map or {}).get(uid)
            if ritmo:
                esperado_u = max(1, round(ritmo * window_days))
                cad = max(1, min(240, round(1440 / ritmo)))
            else:
                esperado_u, cad = esperado, p["cadencia_min"]
        else:
            esperado_u, cad = esperado, 1
        hit = vent.get(uid)
        if hit is None:
            # Activo pero sin ninguna lectura en la ventana => caído.
            ult, antig, nv = None, None, 0
            estado = "caido" if activo else "inactivo"
            motivo = "sin_datos"
        else:
            ult, nv = hit[0], int(hit[1])
            antig = round((ref - ult).total_seconds() / 60, 1)
            if antig > p["warn_min"]:
                estado, motivo = "caido", "sin_lectura"
            elif antig > p["ok_min"]:
                estado, motivo = "warn", "atrasado"
            else:
                # Reciente pero reportando muy por debajo de su ritmo normal.
                base_pct = (100.0 * nv / esperado_u) if esperado_u else 100.0
                estado = "warn" if base_pct < p["degradado_pct"] else "ok"
                motivo = "bajo_ritmo" if estado == "warn" else ""
        if estado == "inactivo":
            esperado_u, pct, perdidos = 0, None, 0
        else:
            pct = round(min(100.0, 100.0 * nv / esperado_u), 1) if esperado_u else 0.0
            perdidos = max(0, esperado_u - nv)
        unidades.append({
            "uid": uid,
            "empresa": empresa,
            "sede": m["sede"],
            "tienda": f"{empresa} {m['sede']}".strip(),
            "grupo": m["grupo"],
            "dispositivo": m["dispositivo"],
            "nombre": m["nombre"],
            "is_main": bool(m["is_main"]) if "is_main" in m else False,
            "activo": activo,
            "estado": estado,
            "motivo": motivo,
            "antiguedad_min": antig,
            "ultima": ult.isoformat() if ult else None,
            "min_ventana": nv,
            "esperado": esperado_u,
            "cadencia_min": cad,
            "pct": pct,
            "perdidos": perdidos,
        })

    resumen = _resumen(unidades)
    dia = ref.astimezone(LIMA)
    return {
        "base": base,
        "label": cfg.label,
        "generado": datetime.now(timezone.utc).isoformat(),
        "fuente": {
            "host": cfg.host,
            "port": cfg.port,
            "dbname": cfg.dbname,
            "max_global": maxg.isoformat() if maxg else None,
            "ref": ref.isoformat(),
            "dia": dia.date().isoformat(),
            "min_actual": dia.hour * 60 + dia.minute,
            "congelada": congelada,
            "antiguedad_min": (round((datetime.now(timezone.utc) - maxg)
                                     .total_seconds() / 60, 1) if maxg else None),
        },
        "umbrales": {"cadencia_min": p["cadencia_min"], "ok_min": p["ok_min"],
                     "warn_min": p["warn_min"]},
        "unidades": unidades,
        "resumen": resumen,
    }


def _blank() -> dict:
    return {"total": 0, "ok": 0, "warn": 0, "caido": 0, "sin_datos": 0,
            "inactivo": 0, "perdidos": 0, "esperado": 0, "estado": "ok", "pct": 100.0}


def _peor(d: dict) -> str:
    if d.get("caido"):
        return "caido"
    if d.get("warn"):
        return "warn"
    if d.get("sin_datos") and not (d.get("ok") or d.get("warn")):
        return "sin_datos"
    return "ok"


def _resumen(unidades: list) -> dict:
    total = _blank()
    por: dict = {}
    for u in unidades:
        e = u["estado"]
        for d in (total, por.setdefault(u["empresa"], _blank())):
            d["total"] += 1
            d[e] = d.get(e, 0) + 1
            d["perdidos"] += u["perdidos"]
            d["esperado"] += u["esperado"]
    for d in [total, *por.values()]:
        d["estado"] = _peor(d)
        d["pct"] = (round(100.0 * (d["esperado"] - d["perdidos"]) / d["esperado"], 1)
                    if d["esperado"] else 100.0)
    return {"total": total,
            "por_empresa": [{"empresa": k, **v} for k, v in sorted(por.items())]}


# ---------- Estado por base ----------------------------------------------------

def _estado(base: str, inv_sql: str, vent_sql: str, con_ritmo: bool = False) -> dict:
    p = PARAMS[base]
    maxg = _scalar(base, "SELECT max(created_at) FROM readings_reading")
    ref, congelada = _ref(maxg)
    ini, fin, esperado = _ventana(ref, congelada, p)
    inv = _rows(base, inv_sql)
    vent = {r[0]: (r[1], r[2]) for r in
            _rows(base, vent_sql, {"ini": ini, "fin": fin})}
    ritmo_map = _ritmo_ambiental(ref) if con_ritmo else None
    window_days = max(1.0 / 1440, (fin - ini).total_seconds() / 86400)
    return _build(base, maxg, ref, congelada, inv, vent, esperado,
                  ritmo_map=ritmo_map, window_days=window_days)


_ESTADO_FN = {
    "energia": lambda: _estado("energia", _INV_ENERGIA, _VENT_ENERGIA),
    "ambiental": lambda: _estado("ambiental", _INV_AMBIENTAL, _VENT_AMBIENTAL,
                                 con_ritmo=True),
}


def estado(base: str, fresh: bool = False) -> dict:
    """Estado en vivo de una base (con caché corta)."""
    if base not in _ESTADO_FN:
        raise ValueError(f"Base desconocida: {base!r}. Usa {list(_ESTADO_FN)}.")
    now = time.time()
    if not fresh:
        hit = _cache.get(base)
        if hit and now - hit[0] < CACHE_TTL:
            data = dict(hit[1])
            data["cache"] = True
            return data
    data = _ESTADO_FN[base]()
    data["cache"] = False
    _cache[base] = (now, data)
    return data


# ---------- Detalle lectura a lectura (minuto a minuto) ------------------------

def lecturas(base: str, ids: list, desde: str, hasta: str) -> dict:
    """Minutos (hora Lima) con lectura por punto/combo, para el heatmap.

    Solo devuelve minutos CON lectura; el navegador infiere los faltantes.
    """
    if base == "energia":
        col, extra = "measurement_point_id", 'AND r."EPpos_value" IS NOT NULL'
    elif base == "ambiental":
        col, extra = "indicator_device_id", ""
    else:
        raise ValueError(f"Base desconocida: {base!r}.")
    ids = [int(i) for i in ids]
    if not ids:
        raise ValueError("Se requiere al menos un id.")
    inlist = ",".join(str(i) for i in ids)
    sql = f"""
        SELECT r.{col} AS uid,
               (r.created_at AT TIME ZONE 'America/Lima')::date AS dia,
               EXTRACT(HOUR   FROM r.created_at AT TIME ZONE 'America/Lima')::int AS hora,
               EXTRACT(MINUTE FROM r.created_at AT TIME ZONE 'America/Lima')::int AS minuto,
               count(*) AS n
        FROM readings_reading r
        WHERE r.{col} IN ({inlist})
          {extra}
          AND r.created_at >= (CAST(:desde AS date))::timestamp AT TIME ZONE 'America/Lima'
          AND r.created_at <  (CAST(:hasta AS date) + 1)::timestamp AT TIME ZONE 'America/Lima'
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
    """
    rows = _rows(base, sql, {"desde": desde, "hasta": hasta})
    out = [{"uid": r[0], "dia": r[1].isoformat(), "hora": r[2],
            "minuto": r[3], "n": r[4]} for r in rows]
    meta = []
    for uid in ids:
        regs = [r for r in out if r["uid"] == uid]
        if not regs:
            meta.append({"uid": uid, "ultimo_dia": None,
                         "ultimo_min_idx": None, "ultima_hhmm": None})
            continue
        ult = regs[-1]
        meta.append({
            "uid": uid, "ultimo_dia": ult["dia"],
            "ultimo_min_idx": ult["hora"] * 60 + ult["minuto"],
            "ultima_hhmm": f"{ult['hora']:02d}:{ult['minuto']:02d}",
        })
    return {"rows": out, "total": len(out), "meta": meta}
