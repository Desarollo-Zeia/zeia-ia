"""Configuración central: carga .env y expone constantes."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# OpenRouter
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Base de datos
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_USER = os.getenv("DB_USER", "Postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = os.getenv("DB_NAME", "Energy")
USE_SSH_TUNNEL = os.getenv("USE_SSH_TUNNEL", "true").strip().lower() in {
    "1", "true", "yes", "on"
}

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

# ElevenLabs (voz: STT + TTS). Sin API key, los endpoints de voz responden
# con error claro y la UI deshabilita el micrófono.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v1")
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")

# Modelo rápido para el resumen HABLADO de las respuestas (la voz no lee el
# texto completo: dice lo esencial en 1-3 frases). Barato y veloz.
VOICE_SUMMARY_MODEL = os.getenv("VOICE_SUMMARY_MODEL", "google/gemini-2.5-flash")
