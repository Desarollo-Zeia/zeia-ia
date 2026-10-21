"""Gateways LoRaWAN de los clientes (esta asignación NO está en las bases).

Mapeo EUI del equipo → gateway, según la lista de despliegue del equipo de
campo (oct-2026). Hoy solo Sanna San Borja. Lo usan el reporte por gateway
(webapp.py) y los avisos (src/avisos_datos.py): si varios equipos de un mismo
gateway se cortan juntos, se manda UN aviso por el gateway.

Ojo: la lista mezcla medidores de ENERGÍA (ADW210/ADW300, base energy) y
sensores AMBIENTALES (AM103/AM107/TS-201-TH, base valhalladb).
"""
from __future__ import annotations

from typing import Optional

GATEWAYS_SANNA = [
    ("Gateway 1 - PB Interior", [
        "009569060004F73E", "00956906000506C8", "009569060004F746",
        "00956906000506F7", "24E124621F210950", "24E124468E392370",
        "24E124468E392350", "24E124621F211202", "24E124621F211992",
        "24E124621F210887", "24E124725C522044", "24E124725C522055",
        "24E124725E038201", "24E124433F330455", "24E124128C069408",
        "24E124128C146616", "24E124128B467474", "24E124621F211055",
        "24E124621F212437"]),
    ("Gateway 2 - PB Exterior", ["24E124621F210952", "24E124725D240633"]),
    ("Gateway 3 - 4to Piso", ["24E124725E033214", "24E124725E032246",
                               "24E124725E038784", "24E124725D244246",
                               "24e124725d243786"]),
]

EUI_A_GATEWAY = {e.upper(): g for g, lst in GATEWAYS_SANNA for e in lst}


def gateway_de(eui: Optional[str]) -> Optional[str]:
    """Nombre del gateway del equipo (None si no está en la lista)."""
    return EUI_A_GATEWAY.get((eui or "").strip().upper())
