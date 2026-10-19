"""Análisis de huecos (gaps) en las lecturas ambientales (valhalladb).

Cobertura diaria por (indicador, sala): días completos, parciales y huecos
medidos contra el ritmo de captura real de cada sensor (mediana). Resumen por
empresa/sede, episodios de hueco y estado de actualidad (staleness).

Track 1 — salas:  readings_reading (indicator_device_id + room_id)  ~15.7M
Track 2 — puntos: readings_readingambiental (indicator_device_id + point_id)

Uso:
    venv/Scripts/python.exe scripts/analisis_huecos_ambiental.py [--export]
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parent.parent

# Configuración desde .env (prefijo AMBIENTAL_*)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
import os  # noqa: E402

DB_HOST = os.getenv("AMBIENTAL_DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("AMBIENTAL_DB_PORT", "5433"))
DB_USER = os.getenv("AMBIENTAL_DB_USER", "postgres")
DB_PASSWORD = os.getenv("AMBIENTAL_DB_PASSWORD", "")
DB_NAME = os.getenv("AMBIENTAL_DB_NAME", "valhalladb")

LECTURAS_DIA = 1440  # teórico 1 lectura/min (solo histórico 2023-2024)
DIA_COMPLETO_FACTOR = 0.9   # >=90% de la mediana del sensor => completo
DIA_PARCIAL_FACTOR = 0.1    # <10% de la mediana => hueco
DIA_HUECO_MIN = 5           # mínimo de lecturas para no ser "hueco"
STALE_DIAS = 2              # >2 días sin datos => alerta de staleness

ANALISIS_DIR = ROOT / "analisis"


def get_engine():
    url = URL.create(
        "postgresql+psycopg2",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            "options": "-c default_transaction_read_only=on -c statement_timeout=300000"
        },
    )


ENGINE = get_engine()


def query(sql: str) -> list:
    with ENGINE.connect() as conn:
        return [list(r) for r in conn.execute(text(sql))]


def episodes(bad_days: list[date]) -> list[tuple[date, date, int]]:
    out: list[tuple[date, date, int]] = []
    for d in bad_days:
        if not out or d != out[-1][1] + timedelta(days=1):
            out.append((d, d, 1))
        else:
            prev_s, prev_e, prev_n = out[-1]
            out[-1] = (prev_s, d, prev_n + 1)
    return out


# ---------- Inventario ----------

INV_SALAS = """
    SELECT rd.id AS ind_dev_id, i.name AS indicador, rd.is_activated,
           d.id AS device_id, d.type_sensor, d.dev_eui AS device_eui,
           d.no_data_status AS status_nd, r.id AS room_id, r.name AS sala,
           r.is_activated AS sala_activa, COALESCE(hq.name, '?') AS sede,
           COALESCE(f.name, '?') AS empresa
    FROM equipments_indicatordevice rd
    JOIN equipments_indicator i ON i.id = rd.indicator_id
    JOIN equipments_device d ON d.id = rd.device_id
    JOIN enterprise_room r ON r.id = d.room_id
    JOIN enterprise_headquarters hq ON hq.id = r.headquarter_id
    JOIN enterprise_enterprise f ON f.id = hq.enterprise_id
    ORDER BY empresa, sede, sala, indicador
"""

INV_PUNTOS = """
    SELECT rd.id AS ind_dev_id, i.name AS indicador, rd.is_activated,
           d.id AS device_id, d.type_sensor, d.dev_eui AS device_eui,
           p.id AS point_id, p.name AS punto, p.is_activated AS punto_activo,
           COALESCE(hq.name, '?') AS sede, COALESCE(f.name, '?') AS empresa
    FROM equipments_indicatorambientaldevice rd
    JOIN equipments_indicator i ON i.id = rd.indicator_id
    JOIN equipments_ambientaldevice d ON d.id = rd.device_id
    JOIN enterprise_measurepoint p ON p.id = d.point_id
    JOIN enterprise_headquartersambiental hq ON hq.id = p.headquarter_id
    JOIN enterprise_enterprise f ON f.id = hq.enterprise_id
    ORDER BY empresa, sede, punto, indicador
"""


def build_summary(name: str, inv: list, dias_d: dict, track: str) -> dict:
    """Analiza un inventario de combos contra sus conteos diarios."""
    combos = {r[0]: {
        "indicador": r[1], "activado": r[2], "device_id": r[3], "sensor": r[4],
        "eui": r[5], "extra": r[6], "lugar": r[7], "latente": r[8],
        "sede": r[9], "empresa": r[10],
    } for r in inv}
    res: dict[int, dict] = {}
    sin_datos: list[int] = []
    global_max_day = max((max(d) for d in dias_d.values()), default=date.today())

    for cid, d in dias_d.items():
        f, u = min(d), max(d)
        ritmo = median(n for n in d.values())
        completo = max(int(ritmo * DIA_COMPLETO_FACTOR), DIA_HUECO_MIN)
        minimo = max(int(ritmo * DIA_PARCIAL_FACTOR), 1)
        n_comp = sum(1 for v in d.values() if v >= completo)
        n_parc = sum(1 for v in d.values() if minimo <= v < completo)
        bad: list[date] = []
        dia = f
        while dia <= u:
            n = d.get(dia, 0)
            if dia not in (f, u) and n < minimo:
                bad.append(dia)
            dia += timedelta(days=1)
        res[cid] = {
            "rango": (f, u), "n_datos": len(d), "ritmo": ritmo,
            "n_completos": n_comp, "n_parciales": n_parc,
            "n_hueco": len(bad), "episodios": episodes(bad),
            "sin_datos_desde": (global_max_day - u).days if u < global_max_day else 0,
        }
    for cid in combos:
        if cid not in dias_d:
            sin_datos.append(cid)
    return {"combos": combos, "res": res, "sin_datos": sin_datos,
            "global_max_day": global_max_day, "track": track}


def report(name: str, st: dict) -> None:
    combos, res, sin_datos = st["combos"], st["res"], st["sin_datos"]

    def fmt(cid: int) -> str:
        c = combos[cid]
        mark = "*" if c["activado"] else " "
        return (f"  {mark}[{c['lugar']} · {c['indicador']}] "
                f"({c['sensor']}/{c['eui'][-4:]})")

    def fmt_linea(cid: int) -> str:
        r = res[cid]
        f, u = r["rango"]
        eps = r["episodios"]
        return (f"{fmt(cid)} ... {f}->{u} | {r['n_datos']} días "
                f"({r['n_completos']} comp, {r['n_parciales']} parc, "
                f"{r['n_hueco']} hueco) | ritmo {r['ritmo']:.0f}/día | "
                f"episodios: {len(eps)}/{sum(e[2] for e in eps)} días"
                + (f" | SIN DATOS desde {u} ({r['sin_datos_desde']} días)"
                   if r["sin_datos_desde"] > STALE_DIAS else ""))

    print("\n" + "=" * 100)
    print(f"{name.upper()} | inventario: {len(combos)} combos | "
          f"con datos: {len(res)} | sin datos: {len(sin_datos)}")
    print("=" * 100)

    grupos = defaultdict(list)
    for cid, c in combos.items():
        grupos[(c["empresa"], c["sede"], c["latente"])].append(cid)
    for (empresa, sede, lat), ids in sorted(grupos.items()):
        con = [i for i in ids if i in res]
        ultimo = max((res[i]["rango"][1] for i in con), default=None)
        activos = [i for i in con if res[i]["sin_datos_desde"] <= STALE_DIAS]
        tot_hueco = sum(len(res[i]["episodios"]) for i in con)
        print(f"\n{empresa} — {sede} ({lat}) | combos: {len(ids)} | "
              f"activos: {len(activos)} | último dato: {ultimo or '—'} "
              f"| episodios hueco: {tot_hueco}")
        for i in ids:
            if i in sin_datos:
                print(f"    [SIN LECTURAS] {fmt(i)}")
                continue
            print(fmt_linea(i))

    print("\n" + "=" * 100)
    print("EPISODIOS DE HUECO (>=1 día bajo el ritmo del sensor) — top 20")
    print("=" * 100)
    todo = sorted(
        ((cid, ef, ee, n) for cid, r in res.items() for ef, ee, n in r["episodios"]),
        key=lambda x: -x[3],
    )
    for cid, ef, ee, n in todo[:20]:
        c = combos[cid]
        print(f"  {n:3d} días  {ef}->{ee} | {c['empresa']}/{c['sede']} · "
              f"{c['lugar']} · {c['indicador']}")

    par = sorted(((cid, r["n_parciales"]) for cid, r in res.items() if r["n_parciales"]),
                 key=lambda x: -x[1])
    if par:
        print("\nDÍAS PARCIALES — top 15")
        for cid, n in par[:15]:
            c = combos[cid]
            print(f"  {n:4d} días | {c['empresa']}/{c['sede']} · {c['lugar']} · {c['indicador']}")


def export(name: str, st: dict) -> None:
    combos, res, sin = st["combos"], st["res"], st["sin_datos"]
    ANALISIS_DIR.mkdir(exist_ok=True)
    out_puntos = []
    for cid, c in combos.items():
        r = res.get(cid)
        if r is None:
            out_puntos.append({"combo_id": cid, "empresa": c["empresa"],
                               "sede": c["sede"], "lugar": c["latente"],
                               "indicador": c["indicador"], "sensor": c["sensor"],
                               "con_datos": False})
            continue
        f, u = r["rango"]
        out_puntos.append({
            "combo_id": cid, "empresa": c["empresa"], "sede": c["sede"],
            "lugar": c["latente"], "indicador": c["indicador"],
            "sensor": c["sensor"], "activado": c["activado"],
            "con_datos": True, "inicio": f.isoformat(), "fin": u.isoformat(),
            "ritmo_diario": int(r["ritmo"]), "dias_datos": r["n_datos"],
            "dias_completos": r["n_completos"], "dias_parciales": r["n_parciales"],
            "dias_hueco": r["n_hueco"],
            "episodios": [[e[0].isoformat(), e[1].isoformat(), e[2]]
                          for e in r["episodios"]],
            "staleness_dias": r["sin_datos_desde"],
        })
    datos = {
        "track": st["track"], "generado": date.today().isoformat(),
        "combos_inventario": len(combos), "combos_con_datos": len(res),
        "combos_sin_datos": len(sin), "global_max_day": st["global_max_day"].isoformat(),
        "puntos": out_puntos,
    }
    p = ANALISIS_DIR / f"huecos_ambiental_{name}.json"
    p.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nExportado: {p}")

    # Cobertura diaria (para el heatmap del dashboard): incluye los días del
    # rango [inicio, fin] de cada combo aunque tengan 0 lecturas.
    filas = []
    for cid, d in st["dias"].items():
        r = res.get(cid)
        if r is None:
            continue
        ritmo = r["ritmo"]
        completo = max(int(ritmo * DIA_COMPLETO_FACTOR), DIA_HUECO_MIN)
        minimo = max(int(ritmo * DIA_PARCIAL_FACTOR), 1)
        f, u = r["rango"]
        dia = f
        while dia <= u:
            n = d.get(dia, 0)
            estado = "completo" if n >= completo else ("parcial" if n >= minimo else "hueco")
            filas.append({"combo_id": cid, "dia": dia.isoformat(), "lecturas": n,
                          "pct": round(100 * n / ritmo, 1) if ritmo else 0,
                          "estado": estado})
            dia += timedelta(days=1)
    p2 = ANALISIS_DIR / f"cobertura_diaria_ambiental_{name}.json"
    p2.write_text(json.dumps(filas, ensure_ascii=False), encoding="utf-8")
    print(f"Exportado: {p2} ({len(filas)} días-combo)")


def main(do_export: bool = False) -> None:
    print("== ANÁLISIS DE HUECOS — AMBIENTAL (valhalladb) ==")
    print(f"DB: {DB_HOST}:{DB_PORT}/{DB_NAME}")

    # Track 1: salas
    print("\n[1/2] Recolectando conteos diarios por (indicador, sala) ...")
    dias: dict[int, dict[date, int]] = defaultdict(dict)
    for cid, dia, n in query("""
        SELECT indicator_device_id,
               (created_at AT TIME ZONE 'America/Lima')::date AS dia,
               count(*) AS n
        FROM readings_reading
        GROUP BY 1, 2
    """):
        dias[cid][dia] = n
    st1 = build_summary("salas", query(INV_SALAS), dias, "salas")
    st1["dias"] = dias

    # Track 2: puntos
    print("[2/2] Recolectando conteos diarios por (indicador, punto) ...")
    dias2: dict[int, dict[date, int]] = defaultdict(dict)
    for cid, dia, n in query("""
        SELECT indicator_device_id,
               (created_at AT TIME ZONE 'America/Lima')::date AS dia,
               count(*) AS n
        FROM readings_readingambiental
        GROUP BY 1, 2
    """):
        dias2[cid][dia] = n
    st2 = build_summary("puntos", query(INV_PUNTOS), dias2, "puntos")
    st2["dias"] = dias2

    report("TRACK 1 — LECTURAS POR SALA (readings_reading)", st1)
    report("TRACK 2 — LECTURAS POR PUNTO (readings_readingambiental)", st2)

    if do_export:
        export("salas", st1)
        export("puntos", st2)


if __name__ == "__main__":
    main(do_export="--export" in sys.argv)
