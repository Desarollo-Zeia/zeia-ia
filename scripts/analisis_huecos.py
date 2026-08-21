"""Análisis de huecos (gaps) en las lecturas de energía.

Cobertura diaria por punto de medición: días completos (>=1300 lecturas),
parciales (<1300) y huecos (<10 lecturas). Resumen por empresa/sede,
episodios de hueco y estado de actualidad (staleness) de cada punto.
Con --export también escribe los datos para el dashboard web:

  analisis/cobertura_diaria.csv    una fila por (punto, día) y su estado
  analisis/cobertura_resumen.json  resumen por punto + episodios + métricas
  (el dashboard los sirve en /gaps, pestaña "Cobertura diaria")

Uso:
    venv/Scripts/python.exe scripts/analisis_huecos.py [--intraday] [--export]
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from src.db import get_engine, validate_sql  # noqa: E402

LECTURAS_DIA = 1440  # 1 lectura/minuto
DIA_COMPLETO = 1300  # >=90% cobertura
DIA_HUECO = 10       # por debajo => hueco

ANALISIS_DIR = Path(__file__).resolve().parent.parent / "analisis"


def query(sql: str) -> list:
    safe = validate_sql(sql)
    with get_engine().connect() as conn:
        return [list(r) for r in conn.execute(text(safe))]


def episodes(bad_days: list[date]) -> list[tuple[date, date, int]]:
    """Agrupa días malos consecutivos en episodios (inicio, fin, n días)."""
    out: list[tuple[date, date, int]] = []
    for d in bad_days:
        if not out or d != out[-1][1] + timedelta(days=1):
            out.append((d, d, 1))
        else:
            prev_s, prev_e, prev_n = out[-1]
            out[-1] = (prev_s, d, prev_n + 1)
    return out


def collect() -> tuple[dict, dict, dict, list[int], date]:
    """Recorre inventario + conteos diarios y devuelve el modelo de datos."""
    inv = query("""
        SELECT mp.id, mp.name, COALESCE(emp.name, '?'), COALESCE(eh.name, '?'),
               COALESCE(ep.name, '?'), COALESCE(d.name, '?'), COALESCE(d.model, '?'),
               mp.is_main, mp.is_active
        FROM enterprises_measurementpoint mp
        LEFT JOIN devices_device d ON d.id = mp.device_id
        LEFT JOIN enterprises_electricalpanel ep ON ep.id = d.electrical_panel_id
        LEFT JOIN enterprises_energyheadquarter eh ON eh.id = ep.energy_headquarter_id
        LEFT JOIN enterprises_enterprise emp ON emp.id = eh.enterprise_id
        ORDER BY 3, 4, 5, 1
    """)
    pts = {r[0]: {"punto": r[1], "empresa": r[2], "sede": r[3], "tablero": r[4],
                  "dispositivo": r[5], "modelo": r[6], "is_main": r[7], "activo": r[8]}
           for r in inv}

    # Conteo diario por punto (pasada pesada sobre ~9M filas).
    # La cadencia se mide sobre EPpos_value (el contador confiable): si una
    # fila llegó sin contador, no cuenta como lectura de energía.
    dias_pts = defaultdict(dict)  # mp_id -> {dia: n}
    for mp_id, dia, n in query("""
        SELECT r.measurement_point_id,
               (r.created_at AT TIME ZONE 'America/Lima')::date AS dia,
               count(*) AS n
        FROM readings_reading r
        WHERE r."EPpos_value" IS NOT NULL
        GROUP BY 1, 2
    """):
        dias_pts[mp_id][dia] = n

    # OJO: el GROUP BY solo devuelve días CON lecturas; los días sin datos no
    # existen como clave y hay que calendarizarlos para contar huecos.
    global_max_day = max((max(d) for d in dias_pts.values()), default=date.today())
    res: dict[int, dict] = {}
    sin_datos: list[int] = []
    for mp_id, d in dias_pts.items():
        f = min(d)
        u = max(d)
        n_completos = sum(1 for v in d.values() if v >= DIA_COMPLETO)
        n_parciales = sum(1 for v in d.values() if DIA_HUECO <= v < DIA_COMPLETO)
        n_hueco = 0
        bad: list[date] = []
        dia = f
        while dia <= u:
            n = d.get(dia, 0)
            if dia not in (f, u) and n < DIA_HUECO:
                bad.append(dia)
                n_hueco += 1
            dia += timedelta(days=1)
        eps = episodes(bad)
        res[mp_id] = {
            "rango": (f, u), "n_datos": len(d), "n_completos": n_completos,
            "n_parciales": n_parciales, "n_hueco": n_hueco, "episodios": eps,
            "sin_datos_desde": (global_max_day - u).days if u < global_max_day else 0,
        }
    for mp_id in pts:
        if mp_id not in dias_pts:
            sin_datos.append(mp_id)
    return pts, dias_pts, res, sin_datos, global_max_day


def export(pts: dict, dias_pts: dict, res: dict, sin_datos: list[int],
           global_max_day: date) -> None:
    """Escribe CSV diario + JSON de resumen para el dashboard web."""
    ANALISIS_DIR.mkdir(exist_ok=True)
    estado_de = lambda n: (  # noqa: E731
        "completo" if n >= DIA_COMPLETO else ("parcial" if n >= DIA_HUECO else "hueco"))

    # CSV diario: una fila por (punto, día) con estado y % de cobertura
    csv_path = ANALISIS_DIR / "cobertura_diaria.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["point_id", "empresa", "sede", "tablero",
                                          "punto", "dia", "lecturas", "pct", "estado"])
        w.writeheader()
        for mp_id, d in sorted(dias_pts.items()):
            p = pts[mp_id]
            for dia, n in sorted(d.items()):
                w.writerow({
                    "point_id": mp_id, "empresa": p["empresa"], "sede": p["sede"],
                    "tablero": p["tablero"], "punto": p["punto"], "dia": dia,
                    "lecturas": n, "pct": round(100 * n / LECTURAS_DIA, 1),
                    "estado": estado_de(n),
                })

    # JSON de resumen: por punto + episodios + puntos sin lecturas
    puntos = []
    for mp_id, p in pts.items():
        r = res.get(mp_id)
        if r is None:
            puntos.append({"point_id": mp_id, "empresa": p["empresa"], "sede": p["sede"],
                           "tablero": p["tablero"], "punto": p["punto"],
                           "modelo": p["modelo"], "is_main": p["is_main"],
                           "con_datos": False})
            continue
        f, u = r["rango"]
        puntos.append({
            "point_id": mp_id, "empresa": p["empresa"], "sede": p["sede"],
            "tablero": p["tablero"], "punto": p["punto"], "modelo": p["modelo"],
            "is_main": p["is_main"], "con_datos": True,
            "inicio": f.isoformat(), "fin": u.isoformat(),
            "dias_datos": r["n_datos"], "dias_completos": r["n_completos"],
            "dias_parciales": r["n_parciales"], "dias_hueco": r["n_hueco"],
            "episodios": [[e[0].isoformat(), e[1].isoformat(), e[2]] for e in r["episodios"]],
            "staleness_dias": r["sin_datos_desde"],
        })
    episodios = sorted(
        ({"point_id": mp_id, "empresa": p["empresa"], "sede": p["sede"],
          "tablero": p["tablero"], "punto": p["punto"], "inicio": ef.isoformat(),
          "fin": ee.isoformat(), "dias": n}
         for mp_id, p in pts.items() if mp_id in res
         for ef, ee, n in res[mp_id]["episodios"]),
        key=lambda x: (-x["dias"], x["inicio"]))
    total_lecturas = sum(n for d in dias_pts.values() for n in d.values())
    meta = {
        "generado": date.today().isoformat(),
        "lecturas_totales": total_lecturas,
        "puntos_inventario": len(pts),
        "puntos_con_datos": len(dias_pts),
        "puntos_sin_datos": len(sin_datos),
        "dias_hueco_total": sum(r["n_hueco"] for r in res.values()),
        "episodios_total": sum(len(r["episodios"]) for r in res.values()),
        "global_max_day": global_max_day.isoformat(),
    }
    out = {"meta": meta, "puntos": puntos, "episodios": episodios[:100]}
    (ANALISIS_DIR / "cobertura_resumen.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nExportado para el dashboard:")
    print(f"  {csv_path}  ({sum(len(d) for d in dias_pts.values()):,} filas)")
    print(f"  {ANALISIS_DIR / 'cobertura_resumen.json'}  ({len(puntos)} puntos)")


def main(include_intraday: bool = False, do_export: bool = False) -> None:
    pts, dias_pts, res, sin_datos, global_max_day = collect()
    print(f"Puntos en inventario: {len(pts)}")
    print(f"Puntos con lecturas: {len(dias_pts)}  "
          f"({sum(len(d) for d in dias_pts.values())} días-punto)")
    if sin_datos:
        print(f"Puntos SIN lecturas: {len(sin_datos)}")

    all_counts = [n for d in dias_pts.values() for n in d.values()]
    if all_counts:
        s = sorted(all_counts)
        med = s[len(s) // 2]
        print(f"Lecturas totales: {sum(all_counts):,} | "
              f"mediana lecturas/día/punto: {med:,} "
              f"({med/LECTURAS_DIA*100:.0f}% del teórico {LECTURAS_DIA})")

    def fmt_punto(mp_id: int) -> str:
        p = pts[mp_id]
        etiqueta = "*" if p["is_main"] else " "
        return (f"  {etiqueta}[{p['punto']}] "
                f"({p['tablero']} · {p['dispositivo']}/{p['modelo']})")

    # Resumen por empresa / sede
    print("\n" + "=" * 100)
    print("RESUMEN POR EMPRESA / SEDE")
    print("=" * 100)
    grandes = defaultdict(list)
    for mp_id, p in pts.items():
        grandes[(p["empresa"], p["sede"])].append(mp_id)
    for (empresa, sede), ids in sorted(grandes.items()):
        activos = [i for i in ids if i in res and res[i]["sin_datos_desde"] <= 2]
        ultimo = max((res[i]["rango"][1] for i in ids if i in res), default=None)
        tot_hueco = sum(ep[2] for i in ids if i in res for ep in res[i]["episodios"])
        sin = [i for i in ids if i in sin_datos]
        print(f"\n{empresa} — {sede}  | puntos: {len(ids)} | con datos: {len(ids)-len(sin)} "
              f"| días-hueco: {tot_hueco} | último dato: {ultimo or '—'} | al día: {len(activos)}")
        for i in ids:
            if i in sin_datos:
                print(f"    [sin lecturas] {fmt_punto(i)}")
                continue
            r = res[i]
            f, u = r["rango"]
            eps = r["episodios"]
            print(f"{fmt_punto(i)} ... {f}->{u} | {r['n_datos']} días con datos "
                  f"({r['n_completos']} completos, {r['n_parciales']} parciales, "
                  f"{r['n_hueco']} hueco) | episodios: "
                  f"{len(eps)}/{sum(e[2] for e in eps)} días"
                  + (f" | SIN DATOS desde {u} ({r['sin_datos_desde']} días)"
                     if r["sin_datos_desde"] > 2 else ""))

    # Episodios de hueco >= 1 día
    print("\n" + "=" * 100)
    print("EPISODIOS DE HUECO (días con <10 lecturas, >= 1 día seguido) — top 30")
    print("=" * 100)
    todo = sorted(
        ((mp_id, ef, ee, n) for mp_id, r in res.items() for ef, ee, n in r["episodios"]),
        key=lambda x: -x[3])
    for mp_id, ef, ee, n in todo[:30]:
        p = pts[mp_id]
        print(f"  {n:3d} días  {ef}->{ee}  | {p['empresa']}/{p['sede']} · "
              f"{p['punto']} ({p['tablero']})")

    # Días parciales por punto
    print("\n" + "=" * 100)
    print("DÍAS PARCIALES (10-1299 lecturas ~ 1%-90% del día) — top 25")
    print("=" * 100)
    par = sorted(((mp_id, r["n_parciales"]) for mp_id, r in res.items() if r["n_parciales"]),
                 key=lambda x: -x[1])
    for mp_id, n in par[:25]:
        p = pts[mp_id]
        print(f"  {n:4d} días  | {p['empresa']}/{p['sede']} · {p['punto']} ({p['tablero']})")

    # Intraday: máximo intervalo sin datos por punto (opcional, pesado)
    if include_intraday:
        print("\n" + "=" * 100)
        print("MÁXIMO INTERVALO INTRA-DÍA SIN LECTURAS (por punto)")
        print("=" * 100)
        try:
            for mp_id, min_gap in query("""
                WITH t AS (
                    SELECT measurement_point_id, created_at,
                           lead(created_at) OVER (
                               PARTITION BY measurement_point_id ORDER BY created_at
                           ) AS nxt
                    FROM readings_reading
                )
                SELECT measurement_point_id,
                       ROUND(EXTRACT(EPOCH FROM (max(nxt - created_at))) / 60.0, 1) AS gap_min
                FROM t
                WHERE nxt IS NOT NULL AND nxt - created_at > interval '10 minutes'
                GROUP BY 1
                ORDER BY 2 DESC NULLS LAST
                LIMIT 30
            """):
                p = pts.get(mp_id, {"punto": mp_id})
                print(f"  {min_gap:8.1f} min | {p.get('empresa','?')}/{p.get('sede','?')} · "
                      f"{p.get('punto', mp_id)} ({p.get('tablero','?')})")
        except Exception as exc:  # timeout u otro: no romper el reporte
            print(f"  (no disponible: {type(exc).__name__}: {exc})")

    if do_export:
        export(pts, dias_pts, res, sin_datos, global_max_day)


if __name__ == "__main__":
    main(include_intraday="--intraday" in sys.argv, do_export="--export" in sys.argv)
