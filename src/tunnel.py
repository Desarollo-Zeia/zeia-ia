"""Gestión del túnel SSH hacia la base de datos.

Si el puerto local ya está abierto (p. ej. túnel levantado a mano o por
pgAdmin), se reutiliza. Si no, se lanza ssh en background.
La reconexión automática ocurre porque db.get_engine() llama a
ensure_tunnel() antes de cada consulta.
"""
from __future__ import annotations

import socket
import subprocess
import time

from . import config

_tunnel_proc: subprocess.Popen | None = None


def port_open(host: str = config.DB_HOST, port: int = config.DB_PORT, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_tunnel() -> None:
    """Garantiza que el túnel está activo. Reutiliza uno existente si lo hay."""
    global _tunnel_proc
    if port_open():
        return  # ya hay un túnel (nuestro o externo)

    cmd = [
        "ssh", "-i", config.SSH_KEY,
        "-N",
        "-L", f"{config.DB_PORT}:{config.SSH_REMOTE_HOST}:{config.SSH_REMOTE_PORT}",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{config.SSH_USER}@{config.SSH_HOST}",
    ]
    _tunnel_proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Esperar a que el puerto quede abierto (máx ~15 s)
    for _ in range(30):
        if port_open():
            return
        if _tunnel_proc.poll() is not None:
            raise RuntimeError(
                "El proceso ssh terminó inesperadamente "
                f"(código {_tunnel_proc.returncode}). Revisa la llave y la red."
            )
        time.sleep(0.5)
    raise RuntimeError("Timeout esperando a que el túnel SSH abra el puerto.")


def close_tunnel() -> None:
    """Cierra el túnel solo si lo lanzamos nosotros."""
    global _tunnel_proc
    if _tunnel_proc and _tunnel_proc.poll() is None:
        _tunnel_proc.terminate()
        try:
            _tunnel_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _tunnel_proc.kill()
    _tunnel_proc = None
