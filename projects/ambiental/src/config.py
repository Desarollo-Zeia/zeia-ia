"""Configuración central: carga .env y expone constantes (proyecto AMBIENTAL, mono-base).

Este proyecto contiene UNA sola base de datos: `valhalladb` (monitoreo ambiental de interiores).
Se mantienen las variables genéricas DB_* y SSH_* como alias de la base
(compatibilidad con scripts y endpoints existentes).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE_NAME = os.getenv("BASE_NAME", "ambiental")    # identificador del módulo
BASE_LABEL = os.getenv("BASE_LABEL", "Ambiental")  # nombre legible


@dataclass(frozen=True)
class DBConfig:
    """Configuración de la base de datos + túnel SSH opcional."""

    name: str            # identificador del módulo
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


# Prefijo de las variables de este módulo (ver .env)
PREFIX = "AMBIENTAL"


def _db_config() -> DBConfig:
    return DBConfig(
        name=BASE_NAME,
        label=BASE_LABEL,
        host=os.getenv(f"{PREFIX}_DB_HOST", "127.0.0.1"),
        port=int(os.getenv(f"{PREFIX}_DB_PORT", "5432")),
        user=os.getenv(f"{PREFIX}_DB_USER", "postgres"),
        password=os.getenv(f"{PREFIX}_DB_PASSWORD", ""),
        dbname=os.getenv(f"{PREFIX}_DB_NAME", ""),
        use_ssh_tunnel=_bool_env(f"USE_SSH_TUNNEL_{PREFIX}"),
        ssh_key=str(ROOT / os.getenv(f"SSH_KEY_{PREFIX}", "")),
        ssh_user=os.getenv(f"SSH_USER_{PREFIX}", "ubuntu"),
        ssh_host=os.getenv(f"SSH_HOST_{PREFIX}", ""),
        ssh_remote_host=os.getenv(f"SSH_REMOTE_HOST_{PREFIX}", ""),
        ssh_remote_port=int(os.getenv(f"SSH_REMOTE_PORT_{PREFIX}", "5432")),
    )


DB = _db_config()
DBS = {DB.name: DB}
DEFAULT_BASE = DB.name


def get_db_config(base: str | None = None) -> DBConfig:
    """Devuelve la config de la base (este proyecto es mono-base: ignora `base`
    y devuelve SIEMPRE esta base; el parámetro se mantiene por compatibilidad)."""
    return DB


# OpenRouter
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Alias genéricos para compatibilidad (DB_* = esta base)
DB_HOST = DB.host
DB_PORT = DB.port
DB_USER = DB.user
DB_PASSWORD = DB.password
DB_NAME = DB.dbname
USE_SSH_TUNNEL = DB.use_ssh_tunnel

SSH_KEY = DB.ssh_key
SSH_USER = DB.ssh_user
SSH_HOST = DB.ssh_host
SSH_REMOTE_HOST = DB.ssh_remote_host
SSH_REMOTE_PORT = DB.ssh_remote_port

# Modelo por defecto (económico y fuerte en SQL); se puede cambiar por CLI.
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen/qwen3-coder")

# ElevenLabs (voz: STT + TTS). Sin API key, los endpoints de voz responden
# con error claro y la UI deshabilita el micrófono.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v1")
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")

# Modelo rápido para el resumen HABLADO de las respuestas (la voz no lee el
# texto completo: dice lo esencial en 1-3 frases con cifras clave vía LLM rápido).
VOICE_SUMMARY_MODEL = os.getenv("VOICE_SUMMARY_MODEL", "google/gemini-2.5-flash")
