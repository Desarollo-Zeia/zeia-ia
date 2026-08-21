"""Configuración central: carga .env y expone constantes.

Soporta DOS bases de datos independientes (módulos del producto):

- `energia`   → PostgreSQL `energy`     (consumo eléctrico, puerto 5432 local)
- `ambiental` → PostgreSQL `valhalladb` (monitoreo ambiental, puerto 5433 local)

Cada una tiene su propia config de conexión y de túnel SSH opcional.
Se conservan las variables genéricas DB_* como alias de ENERGÍA para no
romper scripts/uso existentes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class DBConfig:
    """Configuración de una base de datos + túnel SSH opcional."""

    name: str            # "energia" | "ambiental" (identificador del módulo)
    label: str           # nombre legible
    host: str
    port: int
    user: str
    password: str
    dbname: str
    use_ssh_tunnel: bool
    ssh_key: str
    ssh_user: str
    ssh_host: str
    ssh_remote_host: str
    ssh_remote_port: int


def _bool_env(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).strip().lower() in {"1", "true", "yes", "on"}


def _db_config(prefix: str, name: str, label: str) -> DBConfig:
    return DBConfig(
        name=name,
        label=label,
        host=os.getenv(f"{prefix}_DB_HOST", "127.0.0.1"),
        port=int(os.getenv(f"{prefix}_DB_PORT", "5432")),
        user=os.getenv(f"{prefix}_DB_USER", "postgres"),
        password=os.getenv(f"{prefix}_DB_PASSWORD", ""),
        dbname=os.getenv(f"{prefix}_DB_NAME", ""),
        use_ssh_tunnel=_bool_env(f"USE_SSH_TUNNEL_{prefix}"),
        ssh_key=str(ROOT / os.getenv(f"SSH_KEY_{prefix}", "")),
        ssh_user=os.getenv(f"SSH_USER_{prefix}", "ubuntu"),
        ssh_host=os.getenv(f"SSH_HOST_{prefix}", ""),
        ssh_remote_host=os.getenv(f"SSH_REMOTE_HOST_{prefix}", ""),
        ssh_remote_port=int(os.getenv(f"SSH_REMOTE_PORT_{prefix}", "5432")),
    )


# Configuración de las dos bases
ENERGIA_DB = _db_config("ENERGIA", "energia", "Energía")
AMBIENTAL_DB = _db_config("AMBIENTAL", "ambiental", "Ambiental")

DBS = {ENERGIA_DB.name: ENERGIA_DB, AMBIENTAL_DB.name: AMBIENTAL_DB}
DEFAULT_BASE = "energia"  # módulo por defecto en CLI/webapp


def get_db_config(base: str) -> DBConfig:
    """Devuelve la config de la base pedida ('energia' | 'ambiental')."""
    try:
        return DBS[base]
    except KeyError:
        raise ValueError(f"Base desconocida: {base!r}. Usa una de {list(DBS)}.")


# OpenRouter
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Backward-compat: variables genéricas = ENERGÍA (comportamiento histórico)
DB_HOST = ENERGIA_DB.host
DB_PORT = ENERGIA_DB.port
DB_USER = ENERGIA_DB.user
DB_PASSWORD = ENERGIA_DB.password
DB_NAME = ENERGIA_DB.dbname
USE_SSH_TUNNEL = ENERGIA_DB.use_ssh_tunnel

SSH_KEY = ENERGIA_DB.ssh_key
SSH_USER = ENERGIA_DB.ssh_user
SSH_HOST = ENERGIA_DB.ssh_host
SSH_REMOTE_HOST = ENERGIA_DB.ssh_remote_host
SSH_REMOTE_PORT = ENERGIA_DB.ssh_remote_port

# Modelo por defecto (económico y fuerte en SQL); se puede cambiar por CLI
# Elegido tras la evaluación comparativa (ver eval/results/): mejor balance
# precisión/velocidad/costo; honesto cuando faltan datos.
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen/qwen3-coder")

# ElevenLabs (voz: STT + TTS). Sin API key, los endpoints de voz responden
# con error claro y la UI deshabilita el micrófono.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v1")
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")

# Modelo rápido para el resumen HABLADO de las respuestas (la voz no lee el
# texto completo: dice lo esencial en 1-3 frases). Barato y veloz.
VOICE_SUMMARY_MODEL = os.getenv("VOICE_SUMMARY_MODEL", "google/gemini-2.5-flash")