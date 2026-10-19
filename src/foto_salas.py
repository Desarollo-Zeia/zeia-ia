"""Foto de lo PEOR de HOY por módulo, para los comandos del bot de Telegram.

Tres módulos (un comando cada uno en scripts/telegram_bot.py):
- ambiental → salas (base valhalladb); la sala toma su PEOR sensor
- energia   → equipos / puntos de medición (base energy)
- agua      → medidores de agua (base energy, readings_readingwater)

La imagen muestra SOLO lo que tuvo problemas hoy, de todas las empresas
juntas, de peor a mejor: sin datos ahora → con atraso → con cortes (a igual
gravedad, primero el de menos cobertura). Lo que va bien no aparece (solo se
cuenta). Máximo MAX_FILAS filas: si hay más, un aviso al pie de la imagen y el
detalle en el texto del mensaje. Sin problemas no se arma imagen: solo texto.

Cada fila: barra del día de 00:00 a 24:00 (hora Lima) en tramos de 10 min
(verde = llegaron lecturas, ámbar = hubo cortes en ese tramo, rojo = no llegó
nada, gris = todavía no pasa), % del día con datos y qué pasó.

Reglas:
- Cada lectura "cubre" la cadencia de su sensor = el intervalo más común de
  sus lecturas de hoy (el monitor usa el promedio de 14 días, que se
  distorsiona después de una degradación).
- Un hueco es CORTE si dura max(5 min, cadencia + 2): no cuentan los huecos
  cortos ni UNA lectura perdida suelta (pérdida normal de paquetes).
- Por la última lectura: atraso si pasaron más de max(5, 2.5 × cadencia) min;
  sin datos si pasaron más de max(15, 6 × cadencia) (energía 5/15 y ambiental
  12/30 min, como el monitor; agua cada 30 min: 75/180).
- Solo lo activo: lo que no envía nada hace más de DIAS_ACTIVO días (empresas
  que dejaron de monitorear, puntos que nunca transmitieron) no aparece, ni
  las empresas de prueba (monitor.EXCLUIR).

    from src.foto_salas import foto_peores
    foto = foto_peores("energia")    # foto.png (bytes o None), foto.caption
"""
from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from . import monitor
from .db import get_engine

LIMA = monitor.LIMA
PASO_MIN = 10        # ancho de cada tramo de la barra
MIN_CORTE = 5        # minutos mínimos sin datos para contar un corte
MAX_FILAS = 10       # filas en la imagen; el resto va en el aviso del pie
DIAS_ACTIVO = 30     # sin datos en este plazo = no se monitorea (no aparece)
ANCHO_PX = 960       # ancho de la imagen en px CSS (se renderiza al doble)
ESCALA = 2

MODULOS = {
    "ambiental": {"titulo": "Ambiental", "icono": "🌡", "una": "sala", "varias": "salas",
                  "el": "la", "los": "las",
                  "regla": "corte = 2 o más lecturas seguidas que no llegaron (una suelta no cuenta)"},
    "energia": {"titulo": "Energía", "icono": "⚡", "una": "equipo", "varias": "equipos",
                "el": "el", "los": "los",
                "regla": f"corte = {MIN_CORTE} min o más sin datos"},
    "agua": {"titulo": "Agua", "icono": "💧", "una": "medidor", "varias": "medidores",
             "el": "el", "los": "los",
             "regla": "corte = 2 o más lecturas seguidas que no llegaron (una suelta no cuenta)"},
}
BASE_DE = {"ambiental": "ambiental", "energia": "energia", "agua": "energia"}

ORDEN_ESTADO = {"caido": 0, "atrasado": 1, "ok": 2}
# Gravedad de una fila: 0 sin datos ahora, 1 con atraso, 2 tuvo cortes hoy, 3 bien.
ICONO = {0: "🔴", 1: "🟠", 2: "🟡"}
COLOR = {0: "#ef4444", 1: "#f59e0b", 2: "#facc15", 3: "#22c55e"}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# ---------- Minuto a minuto ---------------------------------------------------

def cortes(falta: list) -> list:
    """[(minuto_inicio, minutos)] de cada tramo seguido sin datos."""
    out, i, n = [], 0, len(falta)
    while i < n:
        if falta[i]:
            j = i
            while j < n and falta[j]:
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def minimo_corte(cadencia: int) -> int:
    """Minutos sin datos desde los que un hueco cuenta como corte."""
    return max(MIN_CORTE, max(1, int(cadencia or 1)) + 2)


def minutos_sin_datos(presentes, n: int, cadencia: int = 1,
                      min_corte: Optional[int] = None) -> list:
    """Lista de n valores (minutos 0..n-1 del día): 1 = sin datos, 0 = con datos.

    Cada lectura cubre `cadencia` minutos desde que llega; un hueco cuenta
    solo si dura `min_corte` o más (por defecto minimo_corte(cadencia)).
    """
    falta = [1] * n
    c = max(1, int(cadencia or 1))
    minimo = minimo_corte(c) if min_corte is None else min_corte
    for m in presentes:
        for x in range(max(0, m), min(n, m + c)):
            falta[x] = 0
    for ini, largo in cortes(falta):
        if largo < minimo:
            falta[ini:ini + largo] = [0] * largo
    return falta


def cadencia_observada(minutos, defecto: int) -> int:
    """Intervalo más común (moda, en min) entre las lecturas del día.

    La moda no se mueve aunque se pierdan paquetes. Con menos de 6
    intervalos (madrugada, sensor lento) se usa `defecto`.
    """
    ms = sorted(set(minutos))
    difs = [b - a for a, b in zip(ms, ms[1:])]
    if len(difs) < 6:
        return max(1, int(defecto or 1))
    cuenta = Counter(difs)
    moda = max(cuenta, key=lambda d: (cuenta[d], -d))    # empate: la menor
    return max(1, min(240, moda))


def tramos(falta: list, paso: int = PASO_MIN, total: int = 1440) -> list:
    """Estado de cada tramo de `paso` min del día: ok | parcial | falta | futuro."""
    n, out = len(falta), []
    for ini in range(0, total, paso):
        pasado = falta[ini:min(ini + paso, n)] if ini < n else []
        if not pasado:
            out.append("futuro")
            continue
        s = sum(pasado)
        out.append("ok" if s == 0 else "falta" if s == len(pasado) else "parcial")
    return out


def umbrales(cadencia: int) -> tuple:
    """(minutos para "con atraso", minutos para "sin datos") según la cadencia."""
    c = max(1, int(cadencia or 1))
    return max(5, round(2.5 * c)), max(15, 6 * c)


def estado_de(antig: Optional[float], cadencia: int) -> str:
    """ok | atrasado | caido según los minutos desde la última lectura."""
    if antig is None:
        return "caido"
    atraso, sin_datos = umbrales(cadencia)
    if antig > sin_datos:
        return "caido"
    return "atrasado" if antig > atraso else "ok"


# ---------- Unidades y filas ----------------------------------------------------

@dataclass
class Unidad:
    """Lo que reporta: sensor (ambiental), punto (energía) o medidor (agua)."""
    uid: int
    tienda: str                  # empresa + sede
    grupo: str                   # sala (ambiental) / tablero (energía) / tubería (agua)
    nombre: str                  # indicador (ambiental) / punto (energía) / medidor (agua)
    presentes: set               # minutos del día con lectura
    cadencia: int                # minutos entre lecturas
    ultima: Optional[datetime]   # última lectura (puede ser de días anteriores)


@dataclass
class Fila:
    nombre: str                  # lo principal: sala / equipo / tubería
    sub: str                     # dónde: tienda (· tablero / medidor)
    tienda: str
    estado: str                  # ok | atrasado | caido
    antig: Optional[float]       # minutos desde la última lectura
    ultima: Optional[datetime]
    falta: list = field(default_factory=list)

    @property
    def cortes(self) -> list:
        return cortes(self.falta)

    @property
    def mayor(self) -> tuple:
        """(inicio, minutos) del corte más largo."""
        return max(self.cortes, key=lambda c: (c[1], -c[0]), default=(0, 0))

    @property
    def cobertura(self) -> float:
        n = len(self.falta)
        return 100.0 * (n - sum(self.falta)) / n if n else 100.0

    @property
    def gravedad(self) -> int:
        if self.estado == "caido":
            return 0
        if self.estado == "atrasado":
            return 1
        return 2 if self.cortes else 3


def ordenar(filas: list) -> list:
    """Lo peor arriba; a igual gravedad, la de menos cobertura."""
    return sorted(filas, key=lambda f: (f.gravedad, round(f.cobertura, 1), f.tienda, f.nombre))


def armar_filas(modulo: str, unidades: list, n: int, ref: datetime) -> list:
    """Una fila por sala (ambiental: su peor sensor), equipo o medidor."""
    evaluadas = []
    for u in unidades:
        falta = minutos_sin_datos(u.presentes, n, u.cadencia)
        antig = (None if u.ultima is None
                 else max(0.0, (ref - u.ultima).total_seconds() / 60))
        evaluadas.append((u, falta, antig, estado_de(antig, u.cadencia)))

    filas = []
    if modulo == "ambiental":
        salas: dict = {}
        for e in evaluadas:
            salas.setdefault((e[0].tienda, e[0].grupo), []).append(e)
        for (tienda, sala), es in salas.items():
            falta = [0] * n
            for _, f, _, _ in es:
                falta = [a | b for a, b in zip(falta, f)]
            # Peor sensor: peor estado y, a igual estado, la lectura más vieja.
            peor = min(es, key=lambda e: (ORDEN_ESTADO[e[3]],
                                          -(e[2] if e[2] is not None else float("inf"))))
            filas.append(Fila(sala, tienda, tienda, peor[3], peor[2], peor[0].ultima, falta))
    else:
        for u, falta, antig, est in evaluadas:
            if modulo == "agua":
                nombre, sub = u.grupo, f"{u.tienda} · {u.nombre}"
            else:
                nombre, sub = u.nombre, f"{u.tienda} · {u.grupo}"
            filas.append(Fila(nombre, sub, u.tienda, est, antig, u.ultima, falta))
    return ordenar(filas)


# ---------- Textos -------------------------------------------------------------

def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _dur(minutos) -> str:
    m = int(round(minutos))
    if m < 60:
        return f"{m} min"
    if m < 1440:
        h, r = divmod(m, 60)
        return f"{h} h {r} min" if r else f"{h} h"
    d, h = m // 1440, (m % 1440) // 60
    return (f"{d} día" if d == 1 else f"{d} días") + (f" {h} h" if h else "")


def _pct(v: float) -> str:
    if v < 100 and round(v, 1) >= 100:
        v = 99.9             # con cortes nunca dice 100%
    return f"{v:.1f}".rstrip("0").rstrip(".") + "%"


def _cuando(t: Optional[datetime], dia: str) -> str:
    """'las 11:40' si es del día mostrado; si no, 'el 17-oct 10:15'."""
    if t is None:
        return "—"
    t = t.astimezone(LIMA)
    if t.date().isoformat() == dia:
        return f"las {t:%H:%M}"
    return f"el {t.day:02d}-{MESES[t.month - 1]} {t:%H:%M}"


def _fecha(dia: str) -> str:
    d = date.fromisoformat(dia)
    return f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"


def detalle(f: Fila, dia: str) -> str:
    """Qué pasó, en una frase corta."""
    if f.estado == "caido":
        if f.ultima is None:
            return "sin lecturas"
        return f"sin datos desde {_cuando(f.ultima, dia)} (hace {_dur(f.antig or 0)})"
    if f.estado == "atrasado":
        return f"última lectura a {_cuando(f.ultima, dia)} (hace {_dur(f.antig or 0)})"
    if f.cortes:
        ini, largo = f.mayor
        rango = f"{_hhmm(ini)}–{_hhmm(ini + largo)} ({_dur(largo)})"
        k = len(f.cortes)
        return f"1 corte: {rango}" if k == 1 else f"{k} cortes; el más largo {rango}"
    return "sin cortes"


def _no_en_vivo(fuente: dict) -> str:
    max_g = fuente.get("max_global")
    t = datetime.fromisoformat(max_g) if max_g else None
    return (f"La base no está en vivo: su última lectura es {_cuando(t, '')}. "
            "Se muestra ese día.")


def caption(modulo: str, filas: list, fuente: dict, max_filas: int = MAX_FILAS) -> str:
    """Texto del mensaje (≤1,024 caracteres): resumen y lo que no entra en la imagen."""
    m = MODULOS[modulo]
    dia, ahora = fuente["dia"], _hhmm(max(0, int(fuente["min_actual"])))
    cuando = f"{_fecha(dia)} hasta {ahora}" if fuente.get("congelada") else f"hoy {ahora}"
    lineas = [f"{m['icono']} {m['titulo']} · {cuando}"]
    if fuente.get("congelada"):
        lineas.append("⚠️ " + _no_en_vivo(fuente))
    total = len(filas)
    problemas = [f for f in filas if f.gravedad < 3]
    if not total:
        lineas.append(f"No hay {m['varias']} con datos en los últimos {DIAS_ACTIVO} días.")
        return "\n".join(lineas)
    if not problemas:
        if total == 1:
            lineas.append(f"✅ Todo bien: {m['el']} {m['una']} envió datos sin cortes hoy.")
        else:
            lineas.append(f"✅ Todo bien: {m['los']} {total} {m['varias']} enviaron datos "
                          "sin cortes hoy.")
        return "\n".join(lineas)
    lineas.append(f"{len(problemas)} de {total} {m['varias']} con problemas · "
                  f"✅ {total - len(problemas)} bien")
    resto = problemas[max_filas:]
    if resto:
        lineas.append(f"⚠️ No entran en la imagen ({len(resto)}):")
        texto = "\n".join(lineas)
        for i, f in enumerate(resto):
            linea = f"{ICONO[f.gravedad]} {f.nombre} ({f.tienda}): {detalle(f, dia)}"
            cola = f"\n… y {len(resto) - i} más"
            if len(texto) + 1 + len(linea) + len(cola) > 1024:
                return texto + cola
            texto += "\n" + linea
        return texto
    return "\n".join(lineas)


# ---------- Imagen ---------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #0b1410; }
#foto { width: __ANCHO__px; padding: 22px 24px 18px; background: #0b1410; color: #e6edf3;
        font-family: "Segoe UI", Roboto, Arial, sans-serif; }
h1 { font-size: 30px; margin: 0; font-weight: 700; }
.subt { color: #8b98a5; font-size: 18px; margin-top: 4px; }
.aviso { margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 17px;
         background: rgba(245,158,11,.15); color: #fbbf24; border: 1px solid #f59e0b; }
.chips { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 12px; }
.chip { padding: 6px 12px; border-radius: 999px; font-size: 17px; font-weight: 600;
        background: #12201b; border: 1px solid #1e332b; }
.eje { position: relative; height: 22px; margin-left: 412px; width: 420px; }
.eje span { position: absolute; top: 0; transform: translateX(-50%); font-size: 14px; color: #8b98a5; }
.fila { display: flex; align-items: center; min-height: 82px; padding: 6px 0; border-top: 1px solid #1e332b; }
.nom { width: 400px; margin-right: 12px; display: flex; align-items: center; gap: 10px; }
.nom > div { min-width: 0; flex: 1; }
.dot { flex: 0 0 14px; height: 14px; border-radius: 50%; }
.n1 { font-size: 21px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.n2 { font-size: 14px; color: #8b98a5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.n3 { font-size: 15px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.barra { position: relative; width: 420px; height: 28px; display: flex; border-radius: 5px; overflow: hidden; }
.barra i { flex: 1 1 0; height: 100%; }
.barra b { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(11,20,16,.6); }
.pct { width: 80px; text-align: right; font-size: 21px; font-weight: 700; font-variant-numeric: tabular-nums; }
.ok { background: #22c55e; } .parcial { background: #f59e0b; }
.falta { background: #ef4444; } .futuro { background: #1f2d27; }
.mas { margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 16px; line-height: 1.35;
       background: rgba(239,68,68,.12); color: #fecaca; border: 1px solid #ef4444; }
.mas b { color: #fca5a5; }
.ley { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 14px; padding-top: 12px;
       border-top: 1px solid #1e332b; font-size: 15px; color: #c3cdd6; }
.ley span { display: flex; align-items: center; gap: 7px; }
.ley i { display: inline-block; width: 16px; height: 16px; border-radius: 3px; }
.pie { margin-top: 8px; font-size: 13px; color: #8b98a5; line-height: 1.4; }
"""


def _color_pct(v: float) -> str:
    return "#2dd4a7" if v >= 95 else "#f59e0b" if v >= 80 else "#f87171"


def _lista_corta(filas: list, maximo: int = 8) -> str:
    nombres = [f"{f.nombre} ({f.tienda})" for f in filas[:maximo]]
    extra = len(filas) - maximo
    return ", ".join(nombres) + (f" y {extra} más" if extra > 0 else "")


def html_foto(modulo: str, filas: list, fuente: dict, generado: datetime,
              max_filas: int = MAX_FILAS) -> str:
    """HTML auto-contenido (sin JS ni recursos externos) con SOLO lo que tiene problemas."""
    e = html.escape
    m = MODULOS[modulo]
    dia, n_min = fuente["dia"], max(0, int(fuente["min_actual"]))
    problemas = [f for f in filas if f.gravedad < 3]
    mostradas, resto = problemas[:max_filas], problemas[max_filas:]
    cuando = _fecha(dia) if fuente.get("congelada") else "Hoy, " + _fecha(dia)

    partes = [f'<div id="foto"><h1>{e(m["titulo"])} · {e(m["varias"])} con problemas</h1>',
              f'<div class="subt">{e(cuando)} · de 00:00 a {_hhmm(n_min)} (hora Lima)</div>']
    if fuente.get("congelada"):
        partes.append(f'<div class="aviso">{e(_no_en_vivo(fuente))}</div>')

    cuenta = Counter(f.gravedad for f in filas)
    chips = [(f"{len(filas)} {m['varias']}", "#e6edf3")]
    for g, txt in ((0, "sin datos ahora"), (1, "con atraso"), (2, "con cortes hoy")):
        if cuenta[g]:
            chips.append((f"{cuenta[g]} {txt}", COLOR[g]))
    chips.append((f"{cuenta[3]} bien", COLOR[3]))
    partes.append('<div class="chips">' + "".join(
        f'<span class="chip" style="color:{c}">{e(t)}</span>' for t, c in chips) + "</div>")

    partes.append('<div class="eje">' + "".join(
        f'<span style="left:{h / 24 * 100:.3f}%">{h:02d}:00</span>' for h in range(0, 25, 3))
        + "</div>")
    lineas = "".join(f'<b style="left:{h / 24 * 100:.3f}%"></b>' for h in range(3, 24, 3))
    for f in mostradas:
        celdas = "".join(f'<i class="{t}"></i>' for t in tramos(f.falta))
        det = detalle(f, dia)
        partes.append(
            '<div class="fila">'
            f'<div class="nom"><span class="dot" style="background:{COLOR[f.gravedad]}"></span>'
            f'<div><div class="n1">{e(f.nombre)}</div><div class="n2">{e(f.sub)}</div>'
            f'<div class="n3" style="color:{COLOR[f.gravedad]}">{e(det[:1].upper() + det[1:])}</div>'
            '</div></div>'
            f'<div class="barra">{celdas}{lineas}</div>'
            f'<div class="pct" style="color:{_color_pct(f.cobertura)}">{_pct(f.cobertura)}</div>'
            "</div>")
    if resto:
        if len(resto) == 1:
            titulo = f"⚠ Hay 1 {m['una']} más con problemas que no entra en la imagen:"
        else:
            titulo = (f"⚠ Hay {len(resto)} {m['varias']} más con problemas que no entran "
                      "en la imagen:")
        partes.append(f'<div class="mas"><b>{e(titulo)}</b> {e(_lista_corta(resto))}. '
                      "El detalle va en el mensaje.</div>")

    partes.append(
        '<div class="ley">'
        '<span><i class="ok"></i>Llegaron lecturas</span>'
        '<span><i class="parcial"></i>Hubo cortes en esos 10 min</span>'
        '<span><i class="falta"></i>No llegó nada</span>'
        '<span><i class="futuro"></i>Todavía no pasa</span></div>'
        f'<div class="pie">Solo aparecen {m["los"]} {m["varias"]} con problemas, de peor a mejor · '
        f'{m["regla"]} · % = parte del día con datos · tramos de 10 min · generado a las '
        f'{generado.astimezone(LIMA):%H:%M}</div></div>')
    return ('<!doctype html><html lang="es"><head><meta charset="utf-8"><style>'
            + _CSS.replace("__ANCHO__", str(ANCHO_PX)) + "</style></head><body>"
            + "".join(partes) + "</body></html>")


def _lanzar_navegador(p):
    """Chromium de Playwright; si no está instalado, Edge o Chrome del sistema."""
    errores = []
    for opciones in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
        try:
            return p.chromium.launch(**opciones)
        except Exception as ex:  # noqa: BLE001 - se prueba el siguiente
            errores.append(str(ex).strip().splitlines()[0][:120])
    raise RuntimeError("No pude abrir un navegador para dibujar la foto: " + " | ".join(errores))


def png_de_html(html_txt: str, alto_aprox: int = 900) -> bytes:
    """Renderiza el HTML con Playwright y devuelve el PNG de #foto."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navegador = _lanzar_navegador(p)
        try:
            pagina = navegador.new_page(viewport={"width": ANCHO_PX, "height": alto_aprox},
                                        device_scale_factor=ESCALA)
            pagina.set_content(html_txt, wait_until="load")
            return pagina.locator("#foto").screenshot(type="png")
        finally:
            navegador.close()


# ---------- Datos en vivo -------------------------------------------------------

def _consulta(base: str, sql: str, params: Optional[dict] = None):
    with get_engine(base).connect() as conn:
        return conn.execute(text(sql), params or {}).fetchall()


# Última lectura en los últimos DIAS_ACTIVO días (por índice: instantáneo).
_ULTIMA = {
    "energia": """
        SELECT measurement_point_id, max(created_at) FROM readings_reading
        WHERE measurement_point_id = ANY(:ids) AND created_at >= :desde
          AND created_at < :fin AND "EPpos_value" IS NOT NULL
        GROUP BY 1""",
    "ambiental": """
        SELECT indicator_device_id, max(created_at) FROM readings_reading
        WHERE indicator_device_id = ANY(:ids) AND created_at >= :desde AND created_at < :fin
        GROUP BY 1""",
    "agua": """
        SELECT measurement_point_water_id, max(created_at) FROM readings_readingwater
        WHERE measurement_point_water_id = ANY(:ids) AND created_at >= :desde
          AND created_at < :fin AND consumo_litros IS NOT NULL
        GROUP BY 1""",
}


def ultimas_lecturas(modulo: str, ids: list, ref: datetime) -> dict:
    """{uid: última lectura} de los que reportaron en los últimos DIAS_ACTIVO días."""
    if not ids:
        return {}
    rows = _consulta(BASE_DE[modulo], _ULTIMA[modulo],
                     {"ids": [int(i) for i in ids],
                      "desde": ref - timedelta(days=DIAS_ACTIVO),
                      "fin": ref + timedelta(minutes=1)})
    return {r[0]: r[1] for r in rows if r[1] is not None}


def _cargar_monitor(modulo: str) -> tuple:
    """Energía o ambiental: inventario y estado del monitor en vivo + minutos de hoy."""
    est = monitor.estado(modulo)
    fuente = est["fuente"]
    ref = datetime.fromisoformat(fuente["ref"])
    dia, n = fuente["dia"], max(1, int(fuente["min_actual"]))   # sin el minuto en curso
    activos = [u for u in est["unidades"] if u["estado"] != "inactivo"]
    con_datos = [u for u in activos if u["min_ventana"]]
    previas = ultimas_lecturas(modulo, [u["uid"] for u in activos if not u["min_ventana"]], ref)
    pres: dict = {}
    if con_datos:
        lect = monitor.lecturas(modulo, [u["uid"] for u in con_datos], dia, dia)
        for r in lect["rows"]:
            if r["dia"] == dia:
                pres.setdefault(r["uid"], set()).add(r["hora"] * 60 + r["minuto"])
    unidades = []
    for u in activos:
        if u["min_ventana"]:
            ultima = datetime.fromisoformat(u["ultima"]) if u["ultima"] else None
        elif u["uid"] in previas:
            ultima = previas[u["uid"]]
        else:
            continue          # nada en DIAS_ACTIVO días: no se está monitoreando
        p = pres.get(u["uid"], set())
        unidades.append(Unidad(u["uid"], u["tienda"], u["grupo"] or "", u["nombre"] or "", p,
                               cadencia_observada(p, u.get("cadencia_min") or 1), ultima))
    return fuente, ref, n, unidades


# Agua (base energy): sede → tubería → medidor. El medidor guarda
# dev_eui_password (credencial): no se lee.
_INV_AGUA = """
    SELECT e.name AS empresa, eh.name AS sede, wp.name AS tuberia,
           mw.id AS uid, mw.name AS nombre
    FROM enterprises_measurementpointwater mw
    JOIN enterprises_waterpipe wp ON wp.id = mw.water_pipe_id
    JOIN enterprises_energyheadquarter eh ON eh.id = wp.energy_headquarter_id
    JOIN enterprises_enterprise e ON e.id = eh.enterprise_id
"""
_DIA_AGUA = """
    SELECT measurement_point_water_id AS uid,
           EXTRACT(HOUR   FROM created_at AT TIME ZONE 'America/Lima')::int AS hora,
           EXTRACT(MINUTE FROM created_at AT TIME ZONE 'America/Lima')::int AS minuto
    FROM readings_readingwater
    WHERE measurement_point_water_id = ANY(:ids)
      AND created_at >= :ini AND created_at < :fin AND consumo_litros IS NOT NULL
    GROUP BY 1, 2, 3
"""
# Ritmo propio de cada medidor (moda del intervalo en 7 días), para la
# madrugada: con pocas lecturas hoy la moda del día no es confiable.
_RITMO_AGUA = """
    WITH x AS (
        SELECT measurement_point_water_id AS uid,
               round(extract(epoch FROM created_at - lag(created_at) OVER (
                   PARTITION BY measurement_point_water_id ORDER BY created_at)) / 60) AS minutos
        FROM readings_readingwater
        WHERE measurement_point_water_id = ANY(:ids)
          AND created_at >= :ini AND created_at < :fin AND consumo_litros IS NOT NULL)
    SELECT uid, mode() WITHIN GROUP (ORDER BY minutos) AS minutos
    FROM x WHERE minutos >= 1 GROUP BY uid
"""


def _cargar_agua() -> tuple:
    """Medidores de agua: inventario, última lectura y minutos de hoy."""
    maxg = _consulta("energia", "SELECT max(created_at) FROM readings_reading")[0][0]
    ahora = datetime.now(timezone.utc)
    congelada = maxg is not None and (ahora - maxg) > timedelta(minutes=monitor.CONGELADA_MIN)
    ref = maxg if congelada else ahora
    rl = ref.astimezone(LIMA)
    ini = datetime(rl.year, rl.month, rl.day, tzinfo=LIMA)
    n = max(1, rl.hour * 60 + rl.minute)
    fuente = {"dia": rl.date().isoformat(), "min_actual": n, "congelada": congelada,
              "max_global": maxg.isoformat() if maxg else None, "ref": ref.isoformat()}
    inv = [r for r in _consulta("energia", _INV_AGUA)
           if (r.empresa or "").strip().lower() not in monitor.EXCLUIR]
    ultimas = ultimas_lecturas("agua", [r.uid for r in inv], ref)
    ids = [r.uid for r in inv if r.uid in ultimas]
    pres: dict = {}
    ritmo: dict = {}
    if ids:
        fin = ref + timedelta(minutes=1)
        for r in _consulta("energia", _DIA_AGUA, {"ids": ids, "ini": ini, "fin": fin}):
            pres.setdefault(r.uid, set()).add(r.hora * 60 + r.minuto)
        ritmo = {r.uid: int(r.minutos) for r in _consulta(
            "energia", _RITMO_AGUA, {"ids": ids, "ini": ref - timedelta(days=7), "fin": fin})
            if r.minutos}
    unidades = []
    for r in inv:
        if r.uid not in ultimas:
            continue
        p = pres.get(r.uid, set())
        unidades.append(Unidad(r.uid, f"{r.empresa} {r.sede}".strip(), r.tuberia or "",
                               r.nombre or "", p, cadencia_observada(p, ritmo.get(r.uid, 10)),
                               ultimas[r.uid]))
    return fuente, ref, n, unidades


_CARGAR = {"ambiental": lambda: _cargar_monitor("ambiental"),
           "energia": lambda: _cargar_monitor("energia"),
           "agua": _cargar_agua}


# ---------- Todo junto ---------------------------------------------------------

@dataclass
class Foto:
    png: Optional[bytes]         # None si no hay problemas (solo texto)
    caption: str
    filas: list                  # todas, de peor a mejor
    problemas: list              # solo las que tienen problemas


def foto_peores(modulo: str, max_filas: int = MAX_FILAS) -> Foto:
    """Consulta en vivo el módulo y arma la foto con lo peor de hoy."""
    if modulo not in MODULOS:
        raise ValueError(f"Módulo desconocido: {modulo!r}. Usa {list(MODULOS)}.")
    fuente, ref, n, unidades = _CARGAR[modulo]()
    filas = armar_filas(modulo, unidades, n, ref)
    problemas = [f for f in filas if f.gravedad < 3]
    texto = caption(modulo, filas, fuente, max_filas)
    if not problemas:
        return Foto(None, texto, filas, problemas)
    alto = 360 + 94 * min(len(problemas), max_filas)
    png = png_de_html(html_foto(modulo, filas, fuente, datetime.now(LIMA), max_filas),
                      alto_aprox=alto)
    return Foto(png, texto, filas, problemas)
