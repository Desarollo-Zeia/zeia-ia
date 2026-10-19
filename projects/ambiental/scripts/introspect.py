"""Extrae el esquema completo de la DB y genera docs/schema.md.

Incluye: tablas, columnas, tipos, PKs, FKs, conteo aproximado de filas
y 3 filas de ejemplo por tabla (para entender el dominio).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src import tunnel
from src.db import describe_table, get_engine, list_schemas, list_tables

SAMPLE_ROWS = 3


def approx_count(conn, schema: str, table: str) -> int | None:
    row = conn.execute(
        text(
            "SELECT reltuples::bigint FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relname = :t"
        ),
        {"s": schema, "t": table},
    ).first()
    return int(row[0]) if row and row[0] is not None and row[0] >= 0 else None


def sample_rows(conn, schema: str, table: str):
    try:
        res = conn.execute(
            text(f'SELECT * FROM "{schema}"."{table}" LIMIT {SAMPLE_ROWS}')
        )
        cols = list(res.keys())
        return cols, [dict(zip(cols, (str(v) for v in r))) for r in res.fetchall()]
    except Exception as e:
        return [], [{"error": str(e)}]


def main() -> int:
    tunnel.ensure_tunnel()
    engine = get_engine()

    lines = ["# Esquema de la base de datos `energy`", ""]
    full = {}

    for schema in list_schemas():
        tables = list_tables(schema)
        lines.append(f"## Esquema `{schema}` ({len(tables)} tablas/vistas)")
        lines.append("")
        with engine.connect() as conn:
            for t in tables:
                name = t["table"]
                info = describe_table(schema, name)
                count = approx_count(conn, schema, name)
                cols, sample = sample_rows(conn, schema, name)

                full[f"{schema}.{name}"] = {**info, "approx_rows": count, "sample": sample}

                header = f"### `{schema}.{name}` ({t['type']}"
                if count is not None:
                    header += f", ~{count:,} filas"
                header += ")"
                lines.append(header)
                lines.append("")
                lines.append("| Columna | Tipo | Nullable | Default |")
                lines.append("|---|---|---|---|")
                for c in info["columns"]:
                    default = c["default"] if c["default"] is not None else ""
                    lines.append(
                        f"| {c['column']} | {c['type']} | {'sí' if c['nullable'] else 'no'} | {default} |"
                    )
                if info["primary_key"]:
                    lines.append(f"\n**PK:** {', '.join(info['primary_key'])}")
                if info["foreign_keys"]:
                    fks = "; ".join(f"{f['column']} → {f['references']}" for f in info["foreign_keys"])
                    lines.append(f"\n**FKs:** {fks}")
                lines.append("")

    out_dir = Path(__file__).resolve().parent.parent / "docs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "schema.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "schema_full.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK - {len(full)} tablas documentadas en docs/schema.md")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        tunnel.close_tunnel()
