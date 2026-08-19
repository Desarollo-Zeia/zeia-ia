"""Análisis de huecos (pérdidas) en lecturas de energía.

Lista CADA hueco (>2 min sin lecturas) por punto de medición dentro de un
rango de fechas, lo registra en la tabla `analisis_huecos` de la DB local
(revisable manualmente) y exporta un CSV.

Uso:
    python scripts/analisis_huecos.py                    # agosto 2026 (por defecto)
    python scripts/analisis_huecos.py --inicio 2026-07-01 --fin 2026-07-31
    python scripts/analisis_huecos.py --min-hueco 10     # solo huecos >= 10 min
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import psycopg2

from src import config

HUE = "America/Lima"


def conectar() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        dbname=config.DB_NAME,
    )


def extraer_huecos(conn, inicio: str, fin: str, min_hueco: int) -> list[dict]:
    """Devuelve cada hueco de lectura: punto, fechas (Lima), duración, lecturas faltantes."""
    sql = """
        WITH g AS (
            SELECT r.measurement_point_id AS mp,
                   r.created_at AS fin,
                   lag(r.created_at) OVER (
                       PARTITION BY r.measurement_point_id ORDER BY r.created_at
                   ) AS inicio,
                   EXTRACT(EPOCH FROM (r.created_at - lag(r.created_at) OVER (
                       PARTITION BY r.measurement_point_id ORDER BY r.created_at
                   ))) / 60.0 AS dur_min
            FROM readings_reading r
            WHERE r.created_at >= %(inicio)s::timestamp AT TIME ZONE 'America/Lima'
              AND r.created_at <  %(fin)s::timestamp AT TIME ZONE 'America/Lima'
        )
        SELECT g.mp AS point_id,
               g.inicio,
               g.fin,
               round(g.dur_min::numeric, 1) AS duracion_min,
               greatest(floor(g.dur_min - 1), 1) AS lecturas_faltantes
        FROM g
        WHERE g.dur_min > %(min_hueco)s
        ORDER BY g.mp, g.inicio
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"inicio": inicio, "fin": fin, "min_hueco": min_hueco})
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def info_puntos(conn) -> dict[int, dict]:
    sql = """
        SELECT mp.id AS point_id, mp.name AS punto, ep.name AS tablero,
               eh.name AS sede, e.name AS empresa
        FROM enterprises_measurementpoint mp
        JOIN devices_device d ON d.id = mp.device_id
        LEFT JOIN enterprises_electricalpanel ep ON ep.id = d.electrical_panel_id
        LEFT JOIN enterprises_energyheadquarter eh ON eh.id = ep.energy_headquarter_id
        LEFT JOIN enterprises_enterprise e ON e.id = eh.enterprise_id
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [c.name for c in cur.description]
        return {row[0]: dict(zip(cols, row)) for row in cur.fetchall()}


def crear_tabla(conn) -> None:
    sql = """
        DROP TABLE IF EXISTS analisis_huecos;
        CREATE TABLE analisis_huecos (
            id SERIAL PRIMARY KEY,
            point_id bigint,
            fecha date,
            inicio timestamptz,
            fin timestamptz,
            duracion_min numeric(8,1),
            lecturas_faltantes integer,
            punto text,
            tablero text,
            sede text,
            empresa text
        );
        CREATE INDEX idx_huecos_point ON analisis_huecos (point_id);
        CREATE INDEX idx_huecos_fecha ON analisis_huecos (fecha);
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def cargar(conn, huecos: list[dict], puntos: dict[int, dict]) -> int:
    sql = """
        INSERT INTO analisis_huecos
            (point_id, fecha, inicio, fin, duracion_min, lecturas_faltantes,
             punto, tablero, sede, empresa)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    rows = []
    for h in huecos:
        info = puntos.get(int(h["point_id"]), {})
        inicio = _en_lima(h["inicio"])
        fin = _en_lima(h["fin"])
        fila = (
            h["point_id"],
            inicio.date(),
            inicio,
            fin,
            h["duracion_min"],
            h["lecturas_faltantes"],
            info.get("punto", "?"),
            info.get("tablero", "?"),
            info.get("sede", "?"),
            info.get("empresa", "?"),
        )
        rows.append(fila)
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def agrupar_eventos(huecos: list[dict], pausa_max_min: float = 10.0) -> list[dict]:
    """Une huecos consecutivos del mismo punto en EVENTOS: si entre el fin de
    un hueco y el inicio del siguiente hay <= pausa_max de lecturas OK, son
    parte del mismo accidente. Un día con 365 huecos pequeños intercalados
    queda como 1 evento con sus totales."""
    eventos = []
    for h in huecos:
        gap_inicio = _en_lima(h["inicio"])
        gap_fin = _en_lima(h["fin"])
        if eventos and eventos[-1]["point_id"] == h["point_id"]:
            ultimo = eventos[-1]
            pausa = (gap_inicio - ultimo["fin"]).total_seconds() / 60.0
            if pausa <= pausa_max_min:
                ultimo["fin"] = gap_fin
                ultimo["minutos_sin_datos"] = round(
                    ultimo["minutos_sin_datos"] + float(h["duracion_min"]), 1)
                ultimo["lecturas_faltantes"] += int(h["lecturas_faltantes"])
                ultimo["n_huecos"] += 1
                ultimo["gap_mayor_min"] = max(ultimo["gap_mayor_min"], float(h["duracion_min"]))
                continue
        eventos.append({
            "point_id": h["point_id"],
            "inicio": gap_inicio,
            "fin": gap_fin,
            "minutos_sin_datos": float(h["duracion_min"]),
            "lecturas_faltantes": int(h["lecturas_faltantes"]),
            "n_huecos": 1,
            "gap_mayor_min": float(h["duracion_min"]),
        })
    return eventos


def _crear_tabla_eventos(conn) -> None:
    sql = """
        DROP TABLE IF EXISTS analisis_eventos;
        CREATE TABLE analisis_eventos (
            id SERIAL PRIMARY KEY,
            point_id bigint,
            fecha date,
            inicio timestamptz,
            fin timestamptz,
            minutos_sin_datos numeric(10,1),
            lecturas_faltantes integer,
            n_huecos integer,
            gap_mayor_min numeric(8,1),
            punto text,
            tablero text,
            sede text,
            empresa text
        );
        CREATE INDEX idx_eventos_point ON analisis_eventos (point_id);
        CREATE INDEX idx_eventos_fecha ON analisis_eventos (fecha);
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def _cargar_eventos(conn, eventos: list[dict], puntos: dict[int, dict]) -> int:
    sql = """
        INSERT INTO analisis_eventos
            (point_id, fecha, inicio, fin, minutos_sin_datos, lecturas_faltantes,
             n_huecos, gap_mayor_min, punto, tablero, sede, empresa)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    rows = []
    for ev in eventos:
        info = puntos.get(int(ev["point_id"]), {})
        rows.append((
            ev["point_id"], ev["inicio"].date(), ev["inicio"], ev["fin"],
            ev["minutos_sin_datos"], ev["lecturas_faltantes"], ev["n_huecos"],
            ev["gap_mayor_min"], info.get("punto", "?"), info.get("tablero", "?"),
            info.get("sede", "?"), info.get("empresa", "?"),
        ))
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def exportar_csv_eventos(eventos: list[dict], puntos: dict[int, dict], ruta: Path) -> None:
    import csv

    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "empresa", "sede", "tablero", "punto", "point_id",
                    "inicio (Lima)", "fin (Lima)", "minutos_sin_datos", "lecturas_faltantes",
                    "n_huecos", "gap_mayor_min"])
        for ev in eventos:
            info = puntos.get(int(ev["point_id"]), {})
            w.writerow([
                ev["inicio"].date().isoformat(),
                info.get("empresa", "?"), info.get("sede", "?"), info.get("tablero", "?"),
                info.get("punto", "?"), ev["point_id"],
                ev["inicio"].strftime("%Y-%m-%d %H:%M:%S"),
                ev["fin"].strftime("%Y-%m-%d %H:%M:%S"),
                ev["minutos_sin_datos"], ev["lecturas_faltantes"],
                ev["n_huecos"], ev["gap_mayor_min"],
            ])
    print(f"CSV de eventos exportado: {ruta}")


def _tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo(HUE)


def _en_lima(dt: datetime) -> datetime:
    """psycopg2 entrega datetimes sin zona (naive, en la zona del servidor=local).
    Les adjunta la zona Lima para tratarlas como fechas locales."""
    tz = _tz()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def exportar_csv(huecos: list[dict], puntos: dict[int, dict], ruta: Path) -> None:
    import csv

    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "empresa", "sede", "tablero", "punto", "point_id",
                    "inicio (Lima)", "fin (Lima)", "duracion_min", "lecturas_faltantes"])
        for h in huecos:
            info = puntos.get(int(h["point_id"]), {})
            inicio = _en_lima(h["inicio"])
            fin = _en_lima(h["fin"])
            w.writerow([
                inicio.date().isoformat(),
                info.get("empresa", "?"),
                info.get("sede", "?"),
                info.get("tablero", "?"),
                info.get("punto", "?"),
                h["point_id"],
                inicio.strftime("%Y-%m-%d %H:%M:%S"),
                fin.strftime("%Y-%m-%d %H:%M:%S"),
                h["duracion_min"],
                h["lecturas_faltantes"],
            ])
    print(f"CSV exportado: {ruta}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inicio", default="2026-08-01", help="fecha inicio (YYYY-MM-DD)")
    ap.add_argument("--fin", default=datetime.now().strftime("%Y-%m-%d"), help="fecha fin exclusiva")
    ap.add_argument("--min-hueco", type=float, default=2.0, help="duración mínima del hueco en minutos")
    ap.add_argument("--merge-max", type=float, default=10.0,
                    help="pausa máxima (min) entre huecos para considerarlos el mismo evento")
    args = ap.parse_args()

    conn = conectar()
    print(f"Extrayendo huecos > {args.min_hueco} min de {args.inicio} a {args.fin} (Lima)...")
    huecos = extraer_huecos(conn, args.inicio, args.fin, args.min_hueco)
    print(f"Huecos encontrados: {len(huecos)}")

    puntos = info_puntos(conn)
    crear_tabla(conn)
    n = cargar(conn, huecos, puntos)
    print(f"Registrados en la tabla del dashboard 'analisis_huecos': {n} filas")

    csv_ruta = Path("analisis") / f"huecos_{args.inicio}_a_{args.fin}.csv"
    exportar_csv(huecos, puntos, csv_ruta)

    eventos = agrupar_eventos(huecos, args.merge_max)
    _crear_tabla_eventos(conn)
    n_ev = _cargar_eventos(conn, eventos, puntos)
    print(f"Registrados en la tabla del dashboard 'analisis_eventos': {n_ev} eventos "
          f"(pausa para agrupar: {args.merge_max} min)")
    exportar_csv_eventos(eventos, puntos,
                         Path("analisis") / f"eventos_{args.inicio}_a_{args.fin}.csv")
    conn.close()


if __name__ == "__main__":
    main()