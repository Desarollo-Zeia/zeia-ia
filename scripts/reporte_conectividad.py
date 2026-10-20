"""Reporte semanal de conectividad (energía, ambiental y agua) en PDF, para jefes.

Una página por empresa con datos: las que enviaron algo en la semana o en las
4 semanas previas (se omiten las empresas de prueba, mismo criterio que el
monitor en vivo). En cada página, una tabla por módulo con una fila por equipo
(energía, agrupados por tablero), por sala (ambiental) o por medidor (agua,
agrupados por tubería), una columna por día (hora de Lima) y el promedio de la
semana. Cada celda = lecturas recibidas / lecturas esperadas del día:

  energía    1,440 por equipo (1 por minuto; filas con contador EPpos, mismo
             criterio que analisis_huecos.py y el monitor en vivo)
  ambiental    288 por sensor (1 cada 5 minutos); cada sala muestra su sensor
               con menos lecturas, así una falla nunca queda escondida en un
               promedio (si en un día en rojo sus sensores difieren, se listan
               debajo uno por uno)
  agua       según el ritmo propio de cada medidor: su intervalo más frecuente
             entre lecturas en las 5 semanas (cada 10 min = 144, cada 30 min =
             48); filas con contador consumo_litros. El agua vive en la base de
             energía: tubería → medidor → readings_readingwater

Los días por debajo del umbral (90%) se pintan en rojo. Solo se incluyen
equipos/sensores con datos en la semana o en las 4 semanas previas (los
circuitos del inventario que nunca se instalaron no cuentan como falla). Los
que enviaron su primera lectura dentro del rango (recién instalados) entran
desde la semana siguiente, para que los días previos a su instalación no salgan
como fallas. Si una empresa tiene varias sedes en la página, cada grupo de la
tabla lleva el nombre de su sede.
No agrupa por gateway: esa asignación no está en la base de datos.

En ambiental, la columna "Batería" muestra la del dispositivo de cada sala: el
último valor que reportó hasta el cierre del rango (historial de uplinks,
buscando hasta 4 semanas antes), en rojo bajo el 20%, y "—" si no reportó. Si
ningún dispositivo de la empresa reporta batería, la columna no aparece.

Cada empresa termina con su conectividad promedio de la semana por módulo (la
última celda de cada tabla) y la conectividad final: el promedio simple de los
módulos que tiene, con las cifras impresas: (96.2% + 99.5%) ÷ 2 = 97.9%. Si
solo tiene un módulo, la final es ese módulo.

Consulta las bases configuradas en .env (en casa: producción por túnel SSH)
con sesión read-only + validación SELECT-only (src.db), arma un HTML
auto-contenido y lo imprime a PDF con Chrome/Edge headless.

Uso:
    python scripts/reporte_conectividad.py              # semana pasada lun-dom, todas las empresas
    python scripts/reporte_conectividad.py --desde 2026-09-28 --hasta 2026-10-04
    python scripts/reporte_conectividad.py --empresa Sanna --empresa Oechsle --html
"""
from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import partial
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from src import tunnel  # noqa: E402
from src.db import get_engine, validate_sql  # noqa: E402
from src.monitor import EXCLUIR as EMPRESAS_PRUEBA  # noqa: E402  (mismas que el monitor)

LIMA = timezone(timedelta(hours=-5))  # Perú no tiene horario de verano
UMBRAL = 90.0                         # % mínimo de un día "normal"
BATERIA_BAJA = 20                     # % de batería que se pinta en rojo
LOOKBACK_DIAS = 28                    # ventana para decidir si un equipo debería reportar
# Lecturas esperadas por día. Agua: solo si el medidor no tiene ritmo medible
# (menos de 2 lecturas en 5 semanas); si no, se usa su ritmo propio.
ESPERADAS = {"energia": 1440, "ambiental": 288, "agua": 144}
BASES = ("energia", "ambiental")                    # bases de datos (.env)
MODULOS = ("energia", "ambiental", "agua")          # módulos del reporte, en este orden
BASE_DE = {"energia": "energia", "ambiental": "ambiental", "agua": "energia"}
TITULOS = {"energia": "Energía", "ambiental": "Ambiental", "agua": "Agua"}
SIN_MODULO = {"energia": "Sin monitoreo de energía", "ambiental": "Sin monitoreo ambiental",
              "agua": "Sin monitoreo de agua"}

DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DIAS_CORTOS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
INDICADORES = {"CO2": "CO₂", "HUMIDITY": "humedad", "TEMPERATURE": "temperatura"}
ORDEN_IND = {"CO2": 0, "HUMIDITY": 1, "TEMPERATURE": 2}

CHROME_CANDIDATOS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# ---------- Consultas (solo lectura) -----------------------------------------

SQL_HORIZONTE = "SELECT max(created_at) FROM readings_reading"

# Inventarios de TODAS las empresas: el reporte se separa por empresa en Python
SQL_INV_ENERGIA = """
    SELECT e.name AS empresa, eh.name AS sede, ep.name AS tablero, mp.id AS uid,
           mp.name AS nombre, COALESCE(mp.is_main, false) AS principal
    FROM enterprises_measurementpoint mp
    JOIN devices_device d ON d.id = mp.device_id
    JOIN enterprises_electricalpanel ep ON ep.id = d.electrical_panel_id
    JOIN enterprises_energyheadquarter eh ON eh.id = ep.energy_headquarter_id
    JOIN enterprises_enterprise e ON e.id = eh.enterprise_id
"""

# Solo filas con contador EPpos (criterio de analisis_huecos.py y del monitor)
SQL_DIAS_ENERGIA = """
    SELECT measurement_point_id AS uid,
           (created_at AT TIME ZONE 'America/Lima')::date AS dia,
           count(*) AS n
    FROM readings_reading
    WHERE measurement_point_id = ANY(:ids)
      AND created_at >= :ini AND created_at < :fin
      AND "EPpos_value" IS NOT NULL
    GROUP BY 1, 2
"""

# ¿El equipo envió algo en las semanas previas? (EXISTS por índice: instantáneo)
SQL_PREVIOS_ENERGIA = """
    SELECT u.id FROM unnest(CAST(:ids AS bigint[])) AS u(id)
    WHERE EXISTS (SELECT 1 FROM readings_reading r
                  WHERE r.measurement_point_id = u.id
                    AND r.created_at >= :ini AND r.created_at < :fin)
"""

SQL_INV_AMBIENTAL = """
    SELECT f.name AS empresa, hq.name AS sede, r.name AS sala, rd.id AS uid,
           i.name AS indicador, r.id AS sala_id
    FROM equipments_indicatordevice rd
    JOIN equipments_indicator i ON i.id = rd.indicator_id
    JOIN equipments_device d ON d.id = rd.device_id
    JOIN enterprise_room r ON r.id = d.room_id
    JOIN enterprise_headquarters hq ON hq.id = r.headquarter_id
    JOIN enterprise_enterprise f ON f.id = hq.enterprise_id
"""

SQL_DIAS_AMBIENTAL = """
    SELECT indicator_device_id AS uid,
           (created_at AT TIME ZONE 'America/Lima')::date AS dia,
           count(*) AS n
    FROM readings_reading
    WHERE indicator_device_id = ANY(:ids)
      AND created_at >= :ini AND created_at < :fin
    GROUP BY 1, 2
"""

SQL_PREVIOS_AMBIENTAL = """
    SELECT u.id FROM unnest(CAST(:ids AS bigint[])) AS u(id)
    WHERE EXISTS (SELECT 1 FROM readings_reading r
                  WHERE r.indicator_device_id = u.id
                    AND r.created_at >= :ini AND r.created_at < :fin)
"""

# Agua (base energy): sede → tubería (enterprises_waterpipe) → medidor
# (enterprises_measurementpointwater). El medidor guarda dev_eui_password
# (credencial del dispositivo): no se lee.
SQL_INV_AGUA = """
    SELECT e.name AS empresa, eh.name AS sede, wp.name AS tuberia, mw.id AS uid,
           mw.name AS nombre, COALESCE(mw.is_main, false) AS principal
    FROM enterprises_measurementpointwater mw
    JOIN enterprises_waterpipe wp ON wp.id = mw.water_pipe_id
    JOIN enterprises_energyheadquarter eh ON eh.id = wp.energy_headquarter_id
    JOIN enterprises_enterprise e ON e.id = eh.enterprise_id
"""

# Solo filas con contador (consumo_litros), como EPpos en energía
SQL_DIAS_AGUA = """
    SELECT measurement_point_water_id AS uid,
           (created_at AT TIME ZONE 'America/Lima')::date AS dia,
           count(*) AS n
    FROM readings_readingwater
    WHERE measurement_point_water_id = ANY(:ids)
      AND created_at >= :ini AND created_at < :fin
      AND consumo_litros IS NOT NULL
    GROUP BY 1, 2
"""

SQL_PREVIOS_AGUA = """
    SELECT u.id FROM unnest(CAST(:ids AS bigint[])) AS u(id)
    WHERE EXISTS (SELECT 1 FROM readings_readingwater r
                  WHERE r.measurement_point_water_id = u.id
                    AND r.created_at >= :ini AND r.created_at < :fin)
"""

# Recién instalados: de los que tienen datos en la semana, los que no tienen
# ninguna lectura antes del inicio del rango (por índice: instantáneo).
SQL_NUEVOS_ENERGIA = """
    SELECT u.id FROM unnest(CAST(:ids AS bigint[])) AS u(id)
    WHERE NOT EXISTS (SELECT 1 FROM readings_reading r
                      WHERE r.measurement_point_id = u.id AND r.created_at < :ini)
"""

SQL_NUEVOS_AMBIENTAL = """
    SELECT u.id FROM unnest(CAST(:ids AS bigint[])) AS u(id)
    WHERE NOT EXISTS (SELECT 1 FROM readings_reading r
                      WHERE r.indicator_device_id = u.id AND r.created_at < :ini)
"""

SQL_NUEVOS_AGUA = """
    SELECT u.id FROM unnest(CAST(:ids AS bigint[])) AS u(id)
    WHERE NOT EXISTS (SELECT 1 FROM readings_readingwater r
                      WHERE r.measurement_point_water_id = u.id AND r.created_at < :ini)
"""

SQL = {
    "energia": {"inv": SQL_INV_ENERGIA, "dias": SQL_DIAS_ENERGIA,
                "previos": SQL_PREVIOS_ENERGIA, "nuevos": SQL_NUEVOS_ENERGIA},
    "ambiental": {"inv": SQL_INV_AMBIENTAL, "dias": SQL_DIAS_AMBIENTAL,
                  "previos": SQL_PREVIOS_AMBIENTAL, "nuevos": SQL_NUEVOS_AMBIENTAL},
    "agua": {"inv": SQL_INV_AGUA, "dias": SQL_DIAS_AGUA,
             "previos": SQL_PREVIOS_AGUA, "nuevos": SQL_NUEVOS_AGUA},
}

# Ritmo propio de cada medidor de agua (cada uno tiene el suyo: 10 o 30 min) =
# su intervalo más frecuente entre lecturas consecutivas, en minutos enteros.
# Se usa la moda y no la mediana porque la moda no se mueve aunque se pierdan
# paquetes (el intervalo normal sigue siendo el más repetido). Los duplicados
# (< 30 s) no cuentan.
SQL_RITMO_AGUA = """
    WITH x AS (
        SELECT measurement_point_water_id AS uid,
               round(extract(epoch FROM created_at - lag(created_at) OVER (
                   PARTITION BY measurement_point_water_id ORDER BY created_at)) / 60) AS minutos
        FROM readings_readingwater
        WHERE measurement_point_water_id = ANY(:ids)
          AND created_at >= :ini AND created_at < :fin
          AND consumo_litros IS NOT NULL)
    SELECT uid, mode() WITHIN GROUP (ORDER BY minutos) AS minutos
    FROM x WHERE minutos >= 1 GROUP BY uid
"""

# Batería de cada dispositivo = el último valor que reportó antes del cierre del
# rango, desde el historial de uplinks (equipments_device.battery es solo la foto
# de ahora y no sirve para semanas pasadas). data_json trae credenciales del
# dispositivo: solo se leen las claves battery y devEUI. El filtro por room_id
# usa su índice (la tabla no tiene índice por created_at).
SQL_BATERIA_AMBIENTAL = """
    SELECT DISTINCT ON (d.id) d.room_id AS sala_id,
           (h.data_json->>'battery')::numeric AS bateria
    FROM historical_readinghistory h
    JOIN equipments_device d
      ON d.room_id = h.room_id AND lower(d.dev_eui) = lower(h.data_json->>'devEUI')
    WHERE h.room_id = ANY(:salas)
      AND h.created_at >= :ini AND h.created_at < :fin
      AND jsonb_typeof(h.data_json->'battery') = 'number'
    ORDER BY d.id, h.created_at DESC
"""


def consultar(base: str, sql: str, **params) -> list:
    seguro = validate_sql(sql)
    with get_engine(base).connect() as conn:
        return conn.execute(text(seguro), params).fetchall()


def _inicio_dia(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=LIMA)


def conteos(base: str, sql: str, ids: list, dias: list[date]) -> dict:
    """{uid: {día Lima: lecturas}} de los días del reporte."""
    ini = _inicio_dia(dias[0])
    fin = _inicio_dia(dias[-1]) + timedelta(days=1)
    out: dict = defaultdict(dict)
    for uid, dia, n in consultar(base, sql, ids=list(ids), ini=ini, fin=fin):
        out[uid][dia] = int(n)
    return dict(out)


def con_datos_previos(base: str, sql: str, ids: list, dias: list[date]) -> set:
    """ids con al menos una lectura en las LOOKBACK_DIAS previas al reporte."""
    if not ids:
        return set()
    fin = _inicio_dia(dias[0])
    ini = fin - timedelta(days=LOOKBACK_DIAS)
    return {r[0] for r in consultar(base, sql, ids=list(ids), ini=ini, fin=fin)}


def cargar(modulo: str, dias: list[date]) -> tuple[list, dict, set, set]:
    """Inventario de todas las empresas del módulo, lecturas por día de la
    semana, qué unidades sin datos en la semana sí enviaron en las previas y
    cuáles enviaron su primera lectura dentro del rango (recién instaladas)."""
    base, sql = BASE_DE[modulo], SQL[modulo]
    inv = consultar(base, sql["inv"])
    ids = [r.uid for r in inv]
    cnt = conteos(base, sql["dias"], ids, dias) if ids else {}
    previos = con_datos_previos(base, sql["previos"], [i for i in ids if i not in cnt], dias)
    nuevos = recien_instalados(base, sql["nuevos"], list(cnt), dias)
    return inv, cnt, previos, nuevos


def recien_instalados(base: str, sql: str, ids: list, dias: list[date]) -> set:
    """De los ids con datos en la semana, los que no tienen ninguna lectura
    anterior al inicio del rango."""
    if not ids:
        return set()
    return {r[0] for r in consultar(base, sql, ids=list(ids), ini=_inicio_dia(dias[0]))}


def cargar_ritmos_agua(inv: list, cnt: dict, previos: set, dias: list[date]) -> dict:
    """{uid: lecturas esperadas por día} de los medidores de agua del reporte,
    según su ritmo propio en la semana y las LOOKBACK_DIAS previas. Los que no
    tienen ritmo medible quedan fuera (se usará ESPERADAS["agua"])."""
    ids = sorted({r.uid for r in inv if r.uid in cnt or r.uid in previos})
    if not ids:
        return {}
    ini = _inicio_dia(dias[0]) - timedelta(days=LOOKBACK_DIAS)
    fin = _inicio_dia(dias[-1]) + timedelta(days=1)
    return {uid: max(1, round(1440 / float(minutos)))
            for uid, minutos in consultar(BASE_DE["agua"], SQL_RITMO_AGUA,
                                          ids=ids, ini=ini, fin=fin)}


def cargar_baterias(inv: list, cnt: dict, previos: set, dias: list[date]) -> dict:
    """{sala_id: batería %} de las salas que entran al reporte: el último valor
    de cada dispositivo hasta el cierre del rango (buscando hasta LOOKBACK_DIAS
    antes del inicio); con varios dispositivos en la sala, el más bajo."""
    salas = sorted({r.sala_id for r in inv if r.uid in cnt or r.uid in previos})
    if not salas:
        return {}
    ini = _inicio_dia(dias[0]) - timedelta(days=LOOKBACK_DIAS)
    fin = _inicio_dia(dias[-1]) + timedelta(days=1)
    out: dict = {}
    for sala_id, valor in consultar("ambiental", SQL_BATERIA_AMBIENTAL,
                                    salas=salas, ini=ini, fin=fin):
        v = float(valor)
        out[sala_id] = min(v, out.get(sala_id, v))
    return out


def verificar_horizonte(base: str, dias: list[date]) -> datetime:
    """Aborta si la base no tiene datos hasta el final del rango (copia vieja)."""
    fin = _inicio_dia(dias[-1]) + timedelta(days=1)
    ult = consultar(base, SQL_HORIZONTE)[0][0]
    if ult is None or ult < fin - timedelta(minutes=10):
        visto = ult.astimezone(LIMA).strftime("%Y-%m-%d %H:%M") if ult else "nunca"
        raise SystemExit(
            f"[{base}] La base solo tiene datos hasta {visto} (Lima) y el reporte "
            f"llega hasta el {dias[-1]}: los últimos días saldrían en rojo sin ser "
            f"fallas. Sincroniza la copia (scripts/sync_db.sh {base}) o usa el túnel "
            "a producción.")
    return ult


# ---------- Modelo del reporte -----------------------------------------------

def _sin_tildes(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def _orden(s: str) -> list:
    """Orden natural, sin tildes ni mayúsculas ('Sala 2' < 'Sala 10')."""
    s = _sin_tildes(s).lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _clave(nombre: str) -> str:
    """Nombre de empresa normalizado para cruzar las dos bases."""
    return " ".join(_sin_tildes(nombre).lower().split())


def pct(n: int, esperadas: int) -> float:
    return min(100.0, 100.0 * n / esperadas) if esperadas else 0.0


def bajo(v: float, umbral: float) -> bool:
    # Se compara el valor redondeado tal como se imprime: una celda que dice
    # "90.0%" nunca sale en rojo y una que dice "89.9%" siempre.
    return round(v, 1) < umbral


def bateria_baja(v: float) -> bool:
    return round(v) < BATERIA_BAJA   # igual que bajo(): el entero que se imprime


def _modulo_por_grupos(inv: list, cnt: dict, previos: set, dias: list[date], nuevos: set,
                       grupo_de, esperadas_de) -> dict | None:
    """Filas por unidad agrupadas en bloques por sede + grupo (energía: tablero;
    agua: tubería), con la principal (is_main) primero en cada bloque."""
    grupos: dict = defaultdict(list)
    excluidos, recientes = 0, []
    for r in inv:
        if r.uid in nuevos:                            # entra la próxima semana
            recientes.append(f"{r.sede} · {grupo_de(r)} · {r.nombre}")
            continue
        if r.uid not in cnt and r.uid not in previos:  # no instalado / retirado
            excluidos += 1
            continue
        esperadas = esperadas_de(r)
        lect = [cnt.get(r.uid, {}).get(d, 0) for d in dias]
        grupos[(r.sede, grupo_de(r))].append({
            "nombre": r.nombre, "principal": bool(r.principal),
            "lecturas": lect, "esperadas": esperadas,
            "vals": [pct(n, esperadas) for n in lect]})
    if not grupos:
        return None
    bloques = []
    for sede, grupo in sorted(grupos, key=lambda k: (_orden(k[0]), _orden(k[1]))):
        filas = sorted(grupos[(sede, grupo)],
                       key=lambda f: (not f["principal"], _orden(f["nombre"])))
        bloques.append({"sede": sede, "titulo": grupo, "filas": filas})
    return {"sedes": sorted({k[0] for k in grupos}, key=_orden), "bloques": bloques,
            "excluidos": excluidos, "nuevos": recientes}


def modulo_energia(inv: list, cnt: dict, previos: set, dias: list[date],
                   nuevos: set = frozenset()) -> dict | None:
    """Módulo de energía de UNA empresa (`inv` = sus filas del inventario)."""
    esperadas = ESPERADAS["energia"]
    mod = _modulo_por_grupos(inv, cnt, previos, dias, nuevos,
                             lambda r: r.tablero, lambda r: esperadas)
    if mod is None:
        return None
    n_filas, n_bloques = sum(len(b["filas"]) for b in mod["bloques"]), len(mod["bloques"])
    mod.update({"clave": "energia", "titulo": TITULOS["energia"], "esperadas": esperadas,
                "col_nombre": "Equipo (agrupado por tablero)",
                "subtitulo": (f"{n_filas} {plural(n_filas, 'equipo', 'equipos')} en "
                              f"{n_bloques} {plural(n_bloques, 'tablero', 'tableros')}"),
                "unidad": ("equipo", "equipos")})
    return mod


def modulo_agua(inv: list, cnt: dict, previos: set, dias: list[date],
                ritmos: dict | None = None, nuevos: set = frozenset()) -> dict | None:
    """Módulo de agua de UNA empresa: medidores agrupados por tubería, cada uno
    contra su ritmo propio (`ritmos` = {uid: lecturas/día} de cargar_ritmos_agua)."""
    ritmos = ritmos or {}
    mod = _modulo_por_grupos(inv, cnt, previos, dias, nuevos, lambda r: r.tuberia,
                             lambda r: ritmos.get(r.uid, ESPERADAS["agua"]))
    if mod is None:
        return None
    n_filas, n_bloques = sum(len(b["filas"]) for b in mod["bloques"]), len(mod["bloques"])
    mod.update({"clave": "agua", "titulo": TITULOS["agua"],
                "col_nombre": "Medidor (agrupado por tubería)",
                "subtitulo": (f"{n_filas} {plural(n_filas, 'medidor', 'medidores')} en "
                              f"{n_bloques} {plural(n_bloques, 'tubería', 'tuberías')}"),
                "unidad": ("medidor", "medidores")})
    return mod


def modulo_ambiental(inv: list, cnt: dict, previos: set, dias: list[date],
                     baterias: dict | None = None,
                     nuevos: set = frozenset()) -> dict | None:
    """Módulo ambiental de UNA empresa (`inv` = sus filas del inventario;
    `baterias` = {sala_id: %} de cargar_baterias)."""
    esperadas = ESPERADAS["ambiental"]
    salas: dict = defaultdict(list)
    bat_sala: dict = {}   # (sede, sala) -> % (la batería es del dispositivo, no del sensor)
    excluidos, recientes = 0, []
    for r in inv:
        if r.uid in nuevos:                            # entra la próxima semana
            recientes.append(f"{r.sede} · {r.sala} · {r.indicador}")
            continue
        if r.uid not in cnt and r.uid not in previos:
            excluidos += 1
            continue
        lect = [cnt.get(r.uid, {}).get(d, 0) for d in dias]
        ind = (r.indicador or "").upper()
        salas[(r.sede, r.sala)].append({
            "indicador": ind, "nombre": INDICADORES.get(ind, r.indicador),
            "lecturas": lect, "esperadas": esperadas,
            "vals": [pct(n, esperadas) for n in lect]})
        b = baterias.get(r.sala_id) if baterias else None
        if b is not None:   # varios dispositivos: el más bajo (como el peor sensor)
            bat_sala[(r.sede, r.sala)] = min(b, bat_sala.get((r.sede, r.sala), b))
    if not salas:
        return None
    sedes = sorted({k[0] for k in salas}, key=_orden)
    por_sede: dict = defaultdict(list)
    n_sensores = 0
    indicadores: set = set()
    for sede, sala in sorted(salas, key=lambda k: (_orden(k[0]), _orden(k[1]))):
        sensores = sorted(salas[(sede, sala)],
                          key=lambda s: (ORDEN_IND.get(s["indicador"], 9), s["nombre"]))
        n_sensores += len(sensores)
        indicadores.update(s["indicador"] for s in sensores)
        idx = range(len(dias))
        # La sala toma su peor sensor del día (una falla nunca queda promediada)
        vals = [min(s["vals"][i] for s in sensores) for i in idx]
        peor = [min(sensores, key=lambda s: s["vals"][i]) for i in idx]
        por_sede[sede].append({
            "nombre": sala, "vals": vals, "esperadas": esperadas,
            "lecturas": [peor[i]["lecturas"][i] for i in idx],
            "todos_sensores": sensores, "bateria": bat_sala.get((sede, sala))})
    bloques = [{"sede": sede, "titulo": None, "filas": por_sede[sede]} for sede in sedes]
    n_salas = sum(len(b["filas"]) for b in bloques)
    nombres_ind = [INDICADORES.get(i, i.lower()) for i in
                   sorted(indicadores, key=lambda i: ORDEN_IND.get(i, 9))]
    return {"clave": "ambiental", "titulo": TITULOS["ambiental"], "sedes": sedes,
            "bloques": bloques, "excluidos": excluidos, "nuevos": recientes,
            "esperadas": esperadas, "col_nombre": "Sala",
            "subtitulo": (f"{n_salas} {plural(n_salas, 'sala', 'salas')} · "
                          f"{n_sensores} {plural(n_sensores, 'sensor', 'sensores')} "
                          f"({unir(nombres_ind)})"),
            "unidad": ("sala", "salas"),
            "con_bateria": bool(bat_sala)}   # sin ningún dato, la columna no aparece


def calcular(mod: dict, umbral: float) -> None:
    """Promedios por fila/día/semana y días bajo el umbral (in-place)."""
    filas = [f for b in mod["bloques"] for f in b["filas"]]
    n = len(filas[0]["vals"])
    for f in filas:
        f["semana"] = mean(f["vals"])
        f["malos"] = [i for i, v in enumerate(f["vals"]) if bajo(v, umbral)]
        # Subfilas por sensor solo si la sala tuvo un día en rojo y sus sensores
        # no fallaron igual ese día (si fallaron igual, la fila de la sala basta)
        todos = f.get("todos_sensores") or []
        difieren = any(fmt(max(s["vals"][i] for s in todos)) != fmt(f["vals"][i])
                       for i in f["malos"]) if todos else False
        f["sensores"] = todos if difieren else []
        for s in f["sensores"]:
            s["semana"] = mean(s["vals"])
    mod["prom_dia"] = [mean(f["vals"][i] for f in filas) for i in range(n)]
    mod["prom_semana"] = mean(mod["prom_dia"])
    mod["n_filas"] = len(filas)
    # Peores primero: más días en rojo, luego el día más bajo
    mod["fallas"] = sorted((f for f in filas if f["malos"]),
                           key=lambda f: (-len(f["malos"]), min(f["vals"])))


def conectividad_final(mods: list[dict]) -> dict:
    """Conectividad final de una empresa = promedio simple de sus módulos.

    Usa las cifras tal como se imprimen (1 decimal) y redondea .5 hacia arriba,
    para que cuadre con la cuenta a mano: (96.2 + 99.5) ÷ 2 = 97.85 → 97.9%.
    Con un solo módulo, la final es ese módulo.
    """
    impresas = [Decimal(f"{m['prom_semana']:.1f}") for m in mods]
    valor = (sum(impresas) / len(impresas)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if len(impresas) == 1:
        nota = f"solo {mods[0]['titulo'].lower()}"
    else:
        nota = "(" + " + ".join(f"{v}%" for v in impresas) + f") ÷ {len(impresas)}"
    return {"valor": float(valor), "nota": nota}


def armar_empresas(datos: dict, dias: list[date], umbral: float,
                   pedidas: list[str] | None = None,
                   baterias: dict | None = None,
                   ritmos: dict | None = None) -> tuple[list[dict], list[str]]:
    """Cruza los módulos por nombre de empresa y arma el reporte de cada una.

    Sin `pedidas`: todas las empresas con datos, en orden alfabético, menos las
    de prueba. Con `pedidas`: solo esas y en ese orden. `baterias` = salida de
    cargar_baterias (columna "Batería" de ambiental) y `ritmos` = salida de
    cargar_ritmos_agua (lecturas esperadas de cada medidor de agua). Devuelve
    (reportes, omitidas), donde omitidas = empresas sin datos en el periodo.
    """
    empresas: dict = {}
    for modulo in MODULOS:
        for r in datos[modulo][0]:
            e = empresas.setdefault(_clave(r.empresa), {
                "nombre": " ".join((r.empresa or "?").split()),
                "filas": defaultdict(list)})
            e["filas"][modulo].append(r)
    if pedidas:
        claves = list(dict.fromkeys((_clave(p), " ".join(p.split())) for p in pedidas))
    else:
        claves = [(k, empresas[k]["nombre"]) for k in sorted(empresas, key=_orden)
                  if k not in EMPRESAS_PRUEBA]
    armadores = {"energia": modulo_energia,
                 "ambiental": partial(modulo_ambiental, baterias=baterias),
                 "agua": partial(modulo_agua, ritmos=ritmos)}
    reportes, omitidas, vistas = [], [], set()
    for k, nombre in claves:
        if k in vistas:
            continue
        vistas.add(k)
        e = empresas.get(k)
        if e is None:
            omitidas.append(f"{nombre} (no existe en las bases)")
            continue
        mods = []
        for modulo in MODULOS:
            if e["filas"][modulo]:
                _, cnt, previos, nuevos = datos[modulo]
                m = armadores[modulo](e["filas"][modulo], cnt, previos, dias, nuevos=nuevos)
                if m:
                    calcular(m, umbral)
                    mods.append(m)
        if not mods:
            omitidas.append(e["nombre"])
            continue
        reportes.append({"empresa": e["nombre"], "modulos": mods,
                         "final": conectividad_final(mods)})
    return reportes, omitidas


# ---------- Texto en español -------------------------------------------------

def plural(n: int, sing: str, plur: str) -> str:
    return sing if n == 1 else plur


def unir(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]


def fmt(v: float) -> str:
    return f"{v:.1f}%"


def fmt_bateria(v: float) -> str:
    return f"{v:.0f}%"


def dia_corto(d: date) -> str:
    return f"{DIAS_CORTOS[d.weekday()]} {d.day:02d}"


def rango_largo(d1: date, d2: date) -> str:
    ini = f"{DIAS_ES[d1.weekday()]} {d1.day:02d}"
    fin = f"{DIAS_ES[d2.weekday()].lower()} {d2.day:02d} de {MESES_ES[d2.month - 1]} de {d2.year}"
    if d1.year != d2.year:
        return f"{ini} de {MESES_ES[d1.month - 1]} de {d1.year} al {fin}"
    if d1.month != d2.month:
        return f"{ini} de {MESES_ES[d1.month - 1]} al {fin}"
    return f"{ini} al {fin}"


# ---------- HTML -------------------------------------------------------------

CSS = """
@page { size: letter; margin: 11mm 12mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: #1f2937; font-size: 10px; line-height: 1.35;
       font-family: -apple-system, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif; }
section.empresa + section.empresa { break-before: page; }
header { border-bottom: 2px solid #111827; padding-bottom: 6px; margin-bottom: 9px; }
h1 { font-size: 16px; margin: 0; color: #111827; }
.rango { font-size: 12px; font-weight: 600; color: #374151; margin-top: 2px; }
.resumen { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
           padding: 7px 10px; margin-bottom: 11px; }
.resumen .linea { margin: 2px 0; font-size: 10.5px; }
.resumen .mod { display: inline-block; min-width: 66px; font-weight: 700; }
.ok { color: #047857; font-weight: 600; }
.mal { color: #b91c1c; font-weight: 700; }
section.modulo { margin-bottom: 11px; break-inside: avoid; }
h2 { font-size: 12.5px; margin: 0 0 4px; color: #111827; }
h2 .sub { font-weight: 400; color: #6b7280; font-size: 10px; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
col.c-nombre { width: 33%; }
col.c-dia { width: 8.25%; }
col.c-sem { width: 9.25%; }
table.con-bat col.c-nombre { width: 25.5%; }
col.c-bat { width: 7.5%; }
th, td { border: 1px solid #e5e7eb; padding: 2.5px 5px; line-height: 1.25; }
thead th { background: #1f2937; color: #fff; font-weight: 600; font-size: 9.5px;
           text-align: center; border-color: #1f2937; }
thead th.nombre { text-align: left; }
td.pct { text-align: center; font-variant-numeric: tabular-nums; }
th.sem, td.sem, th.bat, td.bat { border-left: 2px solid #94a3b8; }
td.sem { font-weight: 700; background: #f8fafc; }
tr.grupo td { background: #eef2f7; font-weight: 700; color: #334155; font-size: 9.5px;
              padding: 2px 5px; }
td.nombre.ind { padding-left: 13px; }
tr.falla td.nombre { border-left: 3px solid #dc2626; }
tr.sensor td { font-size: 9px; color: #6b7280; padding-top: 2px; padding-bottom: 2px; }
tr.sensor td.nombre { padding-left: 26px; }
td.bajo, tr.sensor td.bajo { background: #fee2e2; color: #b91c1c; font-weight: 700; }
tfoot td { background: #f1f5f9; font-weight: 700; border-top: 2px solid #94a3b8; }
section.final { margin-top: 14px; break-inside: avoid; }
section.final td { padding: 6px 5px; font-size: 11.5px; font-weight: 700; }
section.final td.total { font-size: 13.5px; background: #eff6ff; color: #1e3a8a; }
section.final td.total.bajo { background: #fee2e2; color: #b91c1c; }
section.final td.sin { font-size: 9.5px; font-weight: 400; font-style: italic; color: #6b7280; }
section.final .nota { display: block; margin-top: 1px; font-size: 8.5px; font-weight: 400;
                      color: #6b7280; }
"""

esc = html.escape


def _celda(v: float, umbral: float, extra: str = "") -> str:
    clase = "pct" + (" bajo" if bajo(v, umbral) else "") + (f" {extra}" if extra else "")
    return f'<td class="{clase}">{fmt(v)}</td>'


def _celda_bateria(v: float | None) -> str:
    if v is None:   # el dispositivo no reportó batería en la ventana
        return '<td class="pct bat">—</td>'
    clase = "pct" + (" bajo" if bateria_baja(v) else "") + " bat"
    return f'<td class="{clase}">{fmt_bateria(v)}</td>'


def _titulo_bloque(b: dict, varias_sedes: bool) -> str | None:
    """Título del grupo en la tabla: con varias sedes en la página, cada grupo
    lleva su sede (si no, no se sabría de qué sede es cada tablero/tubería)."""
    if not varias_sedes:
        return b["titulo"]
    return f"{b['sede']} · {b['titulo']}" if b["titulo"] else b["sede"]


def _tabla(mod: dict, dias: list[date], umbral: float, varias_sedes: bool = False) -> str:
    # Columna "Batería" (solo ambiental con datos): se llena en la fila de la
    # sala; las subfilas por sensor y el promedio del día la dejan vacía.
    con_bat = mod.get("con_bateria", False)
    vacia = '<td class="bat"></td>' if con_bat else ""
    cols = ('<col class="c-nombre">' + '<col class="c-dia">' * len(dias) + '<col class="c-sem">'
            + ('<col class="c-bat">' if con_bat else ""))
    head = (f'<tr><th class="nombre" scope="col">{esc(mod["col_nombre"])}</th>'
            + "".join(f'<th scope="col">{dia_corto(d)}</th>' for d in dias)
            + '<th class="sem" scope="col">Semana</th>'
            + ('<th class="bat" scope="col">Batería</th>' if con_bat else "") + "</tr>")
    cuerpo = []
    for b in mod["bloques"]:
        titulo = _titulo_bloque(b, varias_sedes)
        if titulo:
            cuerpo.append(f'<tr class="grupo"><td colspan="{len(dias) + 2 + int(con_bat)}">'
                          f'{esc(titulo)}</td></tr>')
        ind = " ind" if titulo else ""
        for f in b["filas"]:
            clase = ' class="falla"' if f["malos"] else ""
            cuerpo.append(f'<tr{clase}><td class="nombre{ind}">{esc(f["nombre"])}</td>'
                          + "".join(_celda(v, umbral) for v in f["vals"])
                          + _celda(f["semana"], umbral, "sem")
                          + (_celda_bateria(f["bateria"]) if con_bat else "") + "</tr>")
            for s in f["sensores"]:   # solo si los sensores de la sala difieren
                cuerpo.append(f'<tr class="sensor"><td class="nombre">↳ {esc(s["nombre"])}</td>'
                              + "".join(_celda(v, umbral) for v in s["vals"])
                              + _celda(s["semana"], umbral, "sem") + vacia + "</tr>")
    pie = ('<tr><td class="nombre">Promedio del día</td>'
           + "".join(_celda(v, umbral) for v in mod["prom_dia"])
           + _celda(mod["prom_semana"], umbral, "sem") + vacia + "</tr>")
    tabla = '<table class="con-bat">' if con_bat else "<table>"
    return (f"{tabla}<colgroup>{cols}</colgroup><thead>{head}</thead>"
            f"<tbody>{''.join(cuerpo)}</tbody><tfoot>{pie}</tfoot></table>")


def _resumen(mod: dict, dias: list[date], umbral: float) -> str:
    sing, plur = mod["unidad"]
    txt = (f"<b>{fmt(mod['prom_semana'])}</b> de las lecturas esperadas · "
           f"{mod['n_filas']} {plural(mod['n_filas'], sing, plur)}")
    if not mod["fallas"]:
        return txt + f' · <span class="ok">ningún día por debajo del {umbral:g}%</span>'
    total = sum(len(f["malos"]) for f in mod["fallas"])
    quienes = [f"{esc(f['nombre'])} ({unir([dia_corto(dias[i]).lower() for i in f['malos']])})"
               for f in mod["fallas"][:3]]
    resto = len(mod["fallas"]) - 3
    return (txt + f' · <span class="mal">{total} {plural(total, "día", "días")} por debajo '
            f'del {umbral:g}%</span>: ' + "; ".join(quienes)
            + (f" y {resto} {plural(resto, sing, plur)} más" if resto > 0 else ""))


def _final(rep: dict, umbral: float) -> str:
    """Cierre de la empresa: conectividad de la semana por módulo y la final."""
    mods = {m["clave"]: m for m in rep["modulos"]}
    celdas = []
    for modulo in MODULOS:
        m = mods.get(modulo)
        celdas.append(_celda(m["prom_semana"], umbral) if m
                      else f'<td class="pct sin">{SIN_MODULO[modulo]}</td>')
    fin = rep["final"]
    clase = "pct total" + (" bajo" if bajo(fin["valor"], umbral) else "")
    celdas.append(f'<td class="{clase}">{fmt(fin["valor"])}'
                  f'<span class="nota">{esc(fin["nota"])}</span></td>')
    head = "".join(f'<th scope="col">{t}</th>'
                   for t in [TITULOS[m] for m in MODULOS] + ["Conectividad final"])
    return ('<section class="final"><h2>Conectividad promedio de la semana</h2>'
            f'<table><thead><tr>{head}</tr></thead>'
            f'<tbody><tr>{"".join(celdas)}</tr></tbody></table></section>')


def _pagina(rep: dict, dias: list[date], umbral: float) -> str:
    """Una empresa: encabezado, resumen, tablas por módulo y conectividad final."""
    mods = rep["modulos"]
    sedes = sorted({s for m in mods for s in m["sedes"]}, key=_orden)
    titulo = rep["empresa"] if len(sedes) != 1 else f"{rep['empresa']} · {sedes[0]}"
    resumen = "".join(f'<div class="linea"><span class="mod">{m["titulo"]}</span>'
                      f'{_resumen(m, dias, umbral)}</div>' for m in mods)
    tablas = "".join(
        f'<section class="modulo"><h2>{m["titulo"]} <span class="sub">· {esc(m["subtitulo"])}'
        f'</span></h2>{_tabla(m, dias, umbral, len(sedes) > 1)}</section>' for m in mods)
    return (f'<section class="empresa"><header><h1>Reporte semanal de conectividad — '
            f'{esc(titulo)}</h1><div class="rango">{rango_largo(dias[0], dias[-1])}</div>'
            f'</header><div class="resumen">{resumen}</div>{tablas}{_final(rep, umbral)}'
            '</section>')


def armar_html(reportes: list[dict], dias: list[date], umbral: float) -> str:
    """Documento completo: una página por empresa."""
    doc_titulo = (f"Conectividad {unir([r['empresa'] for r in reportes])} "
                  f"{dias[0]} al {dias[-1]}")
    paginas = "".join(_pagina(r, dias, umbral) for r in reportes)
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>{esc(doc_titulo)}</title>
<style>{CSS}</style></head><body>
{paginas}
</body></html>"""


# ---------- PDF (Chrome / Edge headless) -------------------------------------

def buscar_chrome(preferido: str | None) -> str:
    candidatos = [preferido, os.environ.get("CHROME_PATH"), *CHROME_CANDIDATOS]
    candidatos += [shutil.which(n) for n in ("google-chrome", "google-chrome-stable",
                                             "chromium", "chromium-browser", "chrome",
                                             "msedge")]
    for c in candidatos:
        if c and Path(c).exists():
            return c
    raise SystemExit("No encontré Chrome/Edge para imprimir el PDF. Indica la ruta con "
                     "--chrome o la variable CHROME_PATH (usa --html para obtener solo el HTML).")


def _pdf_listo(pdf: Path, tam_previo: int) -> tuple[bool, int]:
    """True cuando el PDF existe, termina en %%EOF y ya no crece."""
    try:
        tam = pdf.stat().st_size
        if tam == 0:
            return False, 0
        with open(pdf, "rb") as fh:
            fh.seek(max(0, tam - 1024))
            cola = fh.read()
    except OSError:
        return False, 0
    return (tam == tam_previo and cola.rstrip().endswith(b"%%EOF")), tam


def _cerrar(proc: subprocess.Popen) -> None:
    """Cierra Chrome y sus procesos hijos (renderer, GPU...)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def imprimir_pdf(html_txt: str, destino: Path, chrome: str, timeout: float = 90) -> None:
    """Imprime el HTML a PDF con Chrome headless.

    En macOS Chrome headless escribe el PDF en segundos pero a veces no termina
    el proceso: se espera a que el archivo esté completo y luego se cierra Chrome.
    """
    # mkdtemp + rmtree(ignore_errors): algún proceso hijo de Chrome puede
    # seguir soltando archivos del perfil un instante después de cerrarlo.
    tmp_dir = Path(tempfile.mkdtemp(prefix="zeia-reporte-"))
    try:
        src = tmp_dir / "reporte.html"
        pdf = tmp_dir / "reporte.pdf"
        log = tmp_dir / "chrome.log"
        src.write_text(html_txt, encoding="utf-8")
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               "--disable-background-networking", "--disable-component-update",
               f"--user-data-dir={tmp_dir / 'perfil'}",
               "--no-pdf-header-footer", f"--print-to-pdf={pdf}", src.as_uri()]
        listo, tam = False, -1
        with open(log, "wb") as flog:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=flog,
                                    start_new_session=(os.name != "nt"))
            try:
                limite = time.monotonic() + timeout
                while time.monotonic() < limite:
                    time.sleep(0.3)
                    listo, tam = _pdf_listo(pdf, tam)
                    if listo or (proc.poll() is not None and not pdf.exists()):
                        break
            finally:
                _cerrar(proc)
        if not listo:
            cola = log.read_text(encoding="utf-8", errors="replace")[-1500:]
            raise SystemExit(f"Chrome no generó el PDF en {timeout:.0f} s:\n{cola}")
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, destino)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------- CLI --------------------------------------------------------------

def semana_pasada(hoy: date) -> tuple[date, date]:
    lunes = hoy - timedelta(days=hoy.weekday() + 7)
    return lunes, lunes + timedelta(days=6)


def _slug(s: str) -> str:
    s = _sin_tildes(s).lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "empresa"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reporte semanal de conectividad (PDF).")
    ap.add_argument("--desde", help="AAAA-MM-DD (default: lunes de la semana pasada)")
    ap.add_argument("--hasta", help="AAAA-MM-DD (default: domingo de la semana pasada)")
    ap.add_argument("--empresa", action="append",
                    help="solo esta empresa; repetible y en ese orden "
                         "(default: todas las que tienen datos, en orden alfabético)")
    ap.add_argument("--umbral", type=float, default=UMBRAL, help="%% mínimo (default 90)")
    ap.add_argument("--out", help="ruta del PDF (default: ~/Downloads/reporte_conectividad_...)")
    ap.add_argument("--html", action="store_true", help="guardar también el HTML junto al PDF")
    ap.add_argument("--chrome", help="ruta a Chrome/Edge (si no se detecta solo)")
    args = ap.parse_args(argv)

    hoy = datetime.now(LIMA).date()
    if bool(args.desde) != bool(args.hasta):
        ap.error("usa --desde y --hasta juntos")
    d1, d2 = ((date.fromisoformat(args.desde), date.fromisoformat(args.hasta))
              if args.desde else semana_pasada(hoy))
    if d2 < d1 or (d2 - d1).days > 6:
        ap.error("el rango debe tener entre 1 y 7 días")
    if d2 >= hoy:
        ap.error(f"el {d2} aún no termina (hora de Lima): usa días completos hasta ayer")
    dias = [d1 + timedelta(days=i) for i in range((d2 - d1).days + 1)]

    chrome = buscar_chrome(args.chrome)
    datos = {}
    try:
        for base in BASES:
            verificar_horizonte(base, dias)
        for modulo in MODULOS:
            datos[modulo] = cargar(modulo, dias)
        inv_amb, cnt_amb, previos_amb, _ = datos["ambiental"]
        baterias = cargar_baterias(inv_amb, cnt_amb, previos_amb, dias)
        inv_agua, cnt_agua, previos_agua, _ = datos["agua"]
        ritmos = cargar_ritmos_agua(inv_agua, cnt_agua, previos_agua, dias)
    finally:
        tunnel.close_tunnel()   # solo cierra túneles que abrió este proceso
    reportes, omitidas = armar_empresas(datos, dias, args.umbral, args.empresa,
                                        baterias=baterias, ritmos=ritmos)
    if not reportes:
        raise SystemExit("Ninguna empresa tiene datos en el periodo"
                         + (f": {', '.join(omitidas)}" if omitidas else "") + ".")

    html_txt = armar_html(reportes, dias, args.umbral)
    sufijo = "".join(f"_{_slug(r['empresa'])}" for r in reportes) if args.empresa else ""
    nombre = f"reporte_conectividad{sufijo}_{d1}_a_{d2}.pdf"
    descargas = Path.home() / "Downloads"
    destino = (Path(args.out).expanduser() if args.out
               else (descargas if descargas.is_dir() else Path.cwd()) / nombre)
    imprimir_pdf(html_txt, destino, chrome)
    if args.html:
        destino.with_suffix(".html").write_text(html_txt, encoding="utf-8")

    print(f"PDF: {destino}")
    semanas = LOOKBACK_DIAS // 7 + 1
    for rep in reportes:
        print(f"== {rep['empresa']}")
        for m in rep["modulos"]:
            sing, plur = m["unidad"]
            fuera = (f" · {m['excluidos']} sin datos en {semanas} semanas (no incluidos)"
                     if m["excluidos"] else "")
            print(f"   [{m['clave']}] {m['n_filas']} {plural(m['n_filas'], sing, plur)} · "
                  f"semana {fmt(m['prom_semana'])}{fuera}")
            if m["nuevos"]:
                print("      recién instalados (primera lectura en el rango; entran desde "
                      "la próxima semana): " + "; ".join(m["nuevos"]))
            if m["clave"] == "agua":
                print("      lecturas esperadas/día: " + ", ".join(
                    f"{f['nombre']} ({b['titulo']}) {f['esperadas']}"
                    for b in m["bloques"] for f in b["filas"]))
            print("      días: " + " ".join(fmt(v) for v in m["prom_dia"]))
            for f in m["fallas"]:
                print("      < umbral:", f["nombre"], "→",
                      ", ".join(f"{dias[i]} {fmt(f['vals'][i])}" for i in f["malos"]))
            bajas = [f for b in m["bloques"] for f in b["filas"]
                     if f.get("bateria") is not None and bateria_baja(f["bateria"])]
            if bajas:
                print(f"      batería < {BATERIA_BAJA}%: "
                      + ", ".join(f"{f['nombre']} {fmt_bateria(f['bateria'])}" for f in bajas))
        fin = rep["final"]
        print(f"   Conectividad final: {fmt(fin['valor'])} ({fin['nota']})")
    if omitidas:
        print("Sin datos en el periodo (no incluidas): " + ", ".join(omitidas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
