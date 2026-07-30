"""Configuración central: carga .env y expone constantes."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# OpenRouter
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Base de datos (a través del túnel SSH)
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "55432"))
DB_USER = os.getenv("DB_USER", "Postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = os.getenv("DB_NAME", "Energy")

# Túnel SSH
SSH_KEY = str(ROOT / os.getenv("SSH_KEY", "energy.pem"))
SSH_USER = os.getenv("SSH_USER", "ubuntu")
SSH_HOST = os.getenv("SSH_HOST", "54.242.41.196")
SSH_REMOTE_HOST = os.getenv("SSH_REMOTE_HOST", "172.31.29.136")
SSH_REMOTE_PORT = int(os.getenv("SSH_REMOTE_PORT", "5432"))

# Modelo por defecto (económico y fuerte en SQL); se puede cambiar por CLI
# Elegido tras la evaluación comparativa (ver eval/results/): mejor balance
# precisión/velocidad/costo; honesto cuando faltan datos.
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen/qwen3-coder")
