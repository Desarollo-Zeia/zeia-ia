"""Valida túnel SSH + credenciales + acceso básico a la DB Energy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src import tunnel
from src.db import get_engine, list_schemas


def main() -> int:
    print("1/3  Verificando túnel SSH ...", flush=True)
    tunnel.ensure_tunnel()
    print("      OK - puerto local abierto")

    print("2/3  Conectando a PostgreSQL ...", flush=True)
    engine = get_engine()
    with engine.connect() as conn:
        version, db, user, ro = conn.execute(
            text("SELECT version(), current_database(), current_user, current_setting('transaction_read_only')")
        ).one()
    print(f"      OK - db={db} user={user} read_only={ro}")
    print(f"      {version.split(',')[0]}")

    print("3/3  Esquemas disponibles:", flush=True)
    schemas = list_schemas()
    for s in schemas:
        print(f"      - {s}")

    print("\nConexión verificada correctamente.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        tunnel.close_tunnel()
