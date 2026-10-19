"""Gestión de túneles SSH hacia las bases de datos.

Hay un túnel independiente por base (energía / ambiental). Si el puerto local
ya está abierto (p. ej. túnel levantado a mano o por pgAdmin), se reutiliza.
Si no, se lanza ssh en background.
La reconexión automática ocurre porque db.get_engine() llama a
ensure_tunnel() antes de cada consulta.
"""
from __future__ import annotations

import socket
import subprocess
import time

from . import config
from .config import DBConfig


def port_open(cfg: DBConfig, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((cfg.host, cfg.port), timeout=timeout):
            return True
    except OSError:
        return False


# Reutiliza un túnel abierto por nosotros por base
_tunnels: dict[str, subprocess.Popen] = {}


def ensure_tunnel(base: str | None = None) -> None:
    """Garantiza que el túnel de la base pedida está activo.

    Reutiliza un túnel existente si ya hay algo escuchando en el puerto.
    """
    cfg = config.get_db_config(base or config.DEFAULT_BASE)

    if port_open(cfg):
        return  # ya hay un túnel (nuestro o externo)

    if not cfg.use_ssh_tunnel:
        raise RuntimeError(
            f"[{cfg.label}] No se pudo conectar a la base local en "
            f"{cfg.host}:{cfg.port}; el túnel SSH está desactivado para esta base."
        )

    cmd = [
        "ssh", "-i", cfg.ssh_key,
        "-N",
        "-L", f"{cfg.port}:{cfg.ssh_remote_host}:{cfg.ssh_remote_port}",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{cfg.ssh_user}@{cfg.ssh_host}",
    ]
    _tunnels[cfg.name] = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Esperar a que el puerto quede abierto (máx ~15 s)
    for _ in range(30):
        if port_open(cfg):
            return
        proc = _tunnels.get(cfg.name)
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                f"[{cfg.label}] El proceso ssh terminó inesperadamente "
                f"(código {proc.returncode}). Revisa la llave y la red."
            )
        time.sleep(0.5)
    raise RuntimeError(
        f"[{cfg.label}] Timeout esperando a que el túnel SSH abra el puerto."
    )


def close_tunnel(base: str | None = None) -> None:
    """Cierra el túnel solo si lo lanzamos nosotros (todas o una base)."""
    bases = [base] if base else list(_tunnels)
    for b in bases:
        proc = _tunnels.get(b)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        _tunnels.pop(b, None)