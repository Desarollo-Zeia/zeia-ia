"""Capa de acceso a datos con seguridad de solo lectura.

Defensa en profundidad:
1. Sesión Postgres con default_transaction_read_only=on y statement_timeout.
2. Validación sintáctica: solo se aceptan sentencias SELECT/WITH/EXPLAIN.
3. LIMIT automático si la consulta no lo trae, y truncado del resultado.
"""
from __future__ import annotations

import re

import sqlparse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

from . import config, tunnel

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"vacuum|analyze|call|execute|do|set|comment|refresh|reindex|cluster|"
    r"listen|notify|unlisten|lock|discard|prepare|deallocate)\b",
    re.IGNORECASE,
)
ALLOWED_STARTERS = {"select", "with", "explain", "show", "values", "table"}

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    # Re-verifica el túnel SSH en cada acceso: si el proceso ssh murió
    # (hibernación, corte de red), lo relanza antes de consultar.
    tunnel.ensure_tunnel()
    if _engine is None:
        url = URL.create(
            "postgresql+psycopg2",
            username=config.DB_USER,
            password=config.DB_PASSWORD,
            host=config.DB_HOST,
            port=config.DB_PORT,
            database=config.DB_NAME,
        )
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "options": (
                    "-c default_transaction_read_only=on "
                    # Análisis de varios meses sobre ~120k lecturas/punto toma
                    # ~25s por punto en este servidor; 120s da margen para
                    # consultas multi-punto sin exponer la DB a consultas locas.
                    "-c statement_timeout=120000 "
                    "-c application_name=zeia-agent"
                )
            },
        )
    return _engine


class UnsafeQueryError(ValueError):
    pass


def validate_sql(sql: str) -> str:
    """Valida que el SQL sea de solo lectura. Devuelve el SQL limpio."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise UnsafeQueryError("Consulta vacía.")

    statements = [s for s in sqlparse.split(cleaned) if s.strip()]
    if len(statements) != 1:
        raise UnsafeQueryError("Solo se permite una sentencia por consulta.")

    first = sqlparse.parse(statements[0])[0]
    first_token = next(
        (t for t in first.tokens if not t.is_whitespace and t.ttype not in sqlparse.tokens.Comment),
        None,
    )
    keyword = sqlparse.parse(statements[0])[0].get_type().lower()
    starter = (first_token.value if first_token else "").lower()
    if keyword not in ALLOWED_STARTERS and starter not in ALLOWED_STARTERS:
        raise UnsafeQueryError(
            f"Solo se permiten consultas de lectura (SELECT/WITH/EXPLAIN). Recibido: '{starter or keyword}'."
        )
    if FORBIDDEN.search(cleaned):
        raise UnsafeQueryError("La consulta contiene palabras clave no permitidas.")
    return cleaned


def _has_limit(sql: str) -> bool:
    return bool(re.search(r"\blimit\s+\d+", sql, re.IGNORECASE)) or bool(
        re.search(r"\bfetch\s+first\b", sql, re.IGNORECASE)
    )


def run_query(sql: str, max_rows: int = 200, max_chars: int = 12000) -> dict:
    """Ejecuta un SELECT seguro y devuelve dict con columnas, filas y metadatos."""
    safe_sql = validate_sql(sql)
    if not _has_limit(safe_sql) and safe_sql.lower().startswith(("select", "with", "table")):
        safe_sql = f"{safe_sql} LIMIT {max_rows + 1}"

    with get_engine().connect() as conn:
        result = conn.execute(text(safe_sql))
        columns = list(result.keys())
        rows = [list(r) for r in result.fetchmany(max_rows + 1)]

    truncated = len(rows) > max_rows
    rows = rows[:max_rows]

    # Serializar valores no JSON-friendly
    def norm(v):
        if v is None or isinstance(v, (int, float, str, bool)):
            return v
        return str(v)

    rows = [[norm(v) for v in row] for row in rows]

    out = {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
    text_repr = repr(out)
    if len(text_repr) > max_chars:
        # Recortar filas hasta caber
        while rows and len(repr(out)) > max_chars:
            rows = rows[: max(1, len(rows) // 2)]
            out = {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": True}
        out["note"] = "Resultado recortado por tamaño; refina la consulta o agrega filtros/LIMIT."
    return out


# ---------- Utilidades de introspección (usadas por las herramientas) ----------

def list_schemas() -> list[str]:
    q = """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        ORDER BY schema_name
    """
    with get_engine().connect() as conn:
        return [r[0] for r in conn.execute(text(q))]


def list_tables(schema: str) -> list[dict]:
    q = text("""
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = :schema
        ORDER BY table_name
    """)
    with get_engine().connect() as conn:
        return [
            {"table": r[0], "type": r[1]}
            for r in conn.execute(q, {"schema": schema})
        ]


def describe_table(schema: str, table: str) -> dict:
    cols_q = text("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = :schema AND table_name = :table
        ORDER BY ordinal_position
    """)
    pk_q = text("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = :schema AND tc.table_name = :table
    """)
    fk_q = text("""
        SELECT kcu.column_name, ccu.table_schema AS ref_schema,
               ccu.table_name AS ref_table, ccu.column_name AS ref_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = :schema AND tc.table_name = :table
    """)
    params = {"schema": schema, "table": table}
    with get_engine().connect() as conn:
        columns = [
            {"column": r[0], "type": r[1], "nullable": r[2] == "YES", "default": r[3]}
            for r in conn.execute(cols_q, params)
        ]
        pks = [r[0] for r in conn.execute(pk_q, params)]
        fks = [
            {"column": r[0], "references": f"{r[1]}.{r[2]}.{r[3]}"}
            for r in conn.execute(fk_q, params)
        ]
    return {
        "schema": schema,
        "table": table,
        "columns": columns,
        "primary_key": pks,
        "foreign_keys": fks,
    }
