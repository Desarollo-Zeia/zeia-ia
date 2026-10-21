"""Avisos automáticos en vivo: estado en SQLite + ciclo cada 5 min + backtest.

El bot de Telegram (scripts/telegram_bot.py) lo arranca en un hilo con
`iniciar()`. Modos (AVISOS_MODO en .env):
- apagado → no corre
- simular → evalúa y guarda lo que mandaría, SIN mandar nada (por defecto)
- enviar  → manda los avisos al grupo de TELEGRAM_CHAT_ID
Cada modo tiene su archivo (data/avisos.sqlite y data/avisos_simulacion.sqlite)
para que lo "avisado" en simulación no cuente como enviado al pasar a enviar.

El SQLite es local y solo guarda el estado de los avisos (incidentes,
mensajes, mantenimientos): las bases de producción se leen en solo lectura.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import avisos as av
from . import config
from .avisos_datos import BASES, Historia, SNR_DIAS, VIVO_DIAS

DATA = config.ROOT / "data"
MODO_TXT = {"simular": "en SIMULACIÓN (no se envía nada)", "enviar": "ACTIVOS (se envían al grupo)",
            "apagado": "APAGADOS"}
PENDIENTE_MAX_H = 6               # un aviso que no se pudo enviar se reintenta hasta 6 h
MAX_MANT_MIN = 7 * 1440
MANT_DEFECTO_MIN = 120
DURACION = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)(?:m|min))?$")
FIN = {"fin", "terminar", "termina", "quitar", "cancelar", "stop"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, t TEXT NOT NULL, nivel TEXT, tipo TEXT,
    silencioso INTEGER, texto TEXT NOT NULL, modo TEXT NOT NULL,
    enviado INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS incidentes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, clave TEXT, tipo TEXT, nivel TEXT,
    abierto TEXT, desde TEXT, cerrado TEXT, avisado INTEGER, datos TEXT);
CREATE TABLE IF NOT EXISTS mantenimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alcance TEXT NOT NULL, desde TEXT NOT NULL,
    hasta TEXT NOT NULL, motivo TEXT, por TEXT, creado TEXT NOT NULL);
"""


def _iso(t: datetime) -> str:
    """UTC con segundos fijos: así las fechas se comparan como texto."""
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _error(ex: Exception) -> str:
    txt = (str(ex).strip().splitlines() or [""])[0]
    return f"{type(ex).__name__}: {txt}"[:180]


def ruta_store(modo: str) -> Path:
    return DATA / ("avisos.sqlite" if modo == "enviar" else "avisos_simulacion.sqlite")


class Store:
    """Estado local de los avisos (SQLite). ':memory:' = en memoria (pruebas, --ciclo)."""

    def __init__(self, ruta):
        self.ruta = str(ruta)
        self._lock = threading.Lock()
        self._fija = None
        if self.ruta == ":memory:":
            self._fija = sqlite3.connect(":memory:", check_same_thread=False)
            self._fija.row_factory = sqlite3.Row
        else:
            Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)
        with self._con() as c:
            c.executescript(ESQUEMA)
            if self._fija is None:
                c.execute("PRAGMA journal_mode=WAL")   # el bot lee mientras el motor escribe

    @contextmanager
    def _con(self):
        if self._fija is not None:
            with self._lock, self._fija:
                yield self._fija
            return
        con = sqlite3.connect(self.ruta, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def leer(self, k: str, defecto: Optional[str] = None) -> Optional[str]:
        with self._con() as c:
            fila = c.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
        return fila["v"] if fila else defecto

    def escribir(self, k: str, v: str) -> None:
        with self._con() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, v))

    def estado(self) -> av.Estado:
        return av.Estado.de_json(self.leer("estado"))

    def guardar_estado(self, estado: av.Estado) -> None:
        self.escribir("estado", estado.a_json())

    def registrar(self, m: av.Mensaje, modo: str, enviado: bool, error: str = "") -> None:
        with self._con() as c:
            c.execute("INSERT INTO mensajes (t, nivel, tipo, silencioso, texto, modo, enviado, error)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (_iso(m.t), m.nivel, m.tipo, int(m.silencioso), m.texto, modo,
                       int(enviado), error or None))

    def ultimos(self, n: int = 5) -> list:
        with self._con() as c:
            return c.execute("SELECT * FROM mensajes ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def cierres(self, eventos: list) -> None:
        filas = [(e.inc.clave, e.inc.tipo, e.inc.nivel, _iso(e.inc.abierto), _iso(e.inc.desde),
                  _iso(e.t), int(e.inc.avisado), json.dumps(e.inc.a_dict(), ensure_ascii=False))
                 for e in eventos if e.tipo == "cierra"]
        if filas:
            with self._con() as c:
                c.executemany("INSERT INTO incidentes (clave, tipo, nivel, abierto, desde, cerrado,"
                              " avisado, datos) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", filas)

    def mantenimientos(self, desde: datetime, hasta: Optional[datetime] = None) -> list:
        """Los que se cruzan con [desde, hasta] (sin `hasta`: los vigentes en `desde`)."""
        with self._con() as c:
            filas = c.execute("SELECT * FROM mantenimientos WHERE desde <= ? AND hasta > ? "
                              "ORDER BY hasta", (_iso(hasta or desde), _iso(desde))).fetchall()
        return [av.Mantenimiento(f["alcance"], datetime.fromisoformat(f["hasta"]),
                                 datetime.fromisoformat(f["desde"]), f["motivo"] or "",
                                 f["por"] or "", f["id"]) for f in filas]

    def poner_mantenimiento(self, alcance: str, desde: datetime, hasta: datetime,
                            motivo: str = "", por: str = "") -> av.Mantenimiento:
        with self._con() as c:
            cur = c.execute("INSERT INTO mantenimientos (alcance, desde, hasta, motivo, por, creado)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (alcance, _iso(desde), _iso(hasta), motivo, por, _iso(desde)))
        return av.Mantenimiento(alcance, hasta, desde, motivo, por, cur.lastrowid)

    def terminar_mantenimientos(self, t: datetime, alcance: Optional[str] = None) -> list:
        """Termina los vigentes (todos o los de ese alcance). Devuelve los terminados."""
        vigentes = self.mantenimientos(t)
        if alcance:
            a = av.norm(alcance)
            vigentes = [m for m in vigentes if a in av.norm(m.alcance)]
        with self._con() as c:
            for m in vigentes:
                c.execute("UPDATE mantenimientos SET hasta = ? WHERE id = ?", (_iso(t), m.id))
        return vigentes


def _msg_dict(m: av.Mensaje) -> dict:
    return {"texto": m.texto, "nivel": m.nivel, "silencioso": m.silencioso,
            "t": _iso(m.t), "tipo": m.tipo}


def _msg_de(d: dict) -> av.Mensaje:
    return av.Mensaje(d["texto"], d["nivel"], bool(d["silencioso"]),
                      datetime.fromisoformat(d["t"]), d.get("tipo", "aviso"))


def _u_dict(u: av.Unidad) -> dict:
    return {"clave": u.clave, "modulo": u.modulo, "nombre": u.nombre, "grupo": u.grupo,
            "empresa": u.empresa, "sede": u.sede, "gateway": u.gateway, "cadencia": u.cadencia}


def _enviar_telegram(texto: str, silencioso: bool) -> None:
    from . import telegram
    telegram.send_message(texto, silent=silencioso)


class Motor:
    """Un ciclo cada `ciclo_min`: lee lo nuevo, evalúa, avisa (o simula) y guarda."""

    def __init__(self, modo: Optional[str] = None, store: Optional[Store] = None,
                 reglas: Optional[av.Reglas] = None, historia: Optional[Historia] = None,
                 enviar: Optional[Callable] = None, ahora: Optional[Callable] = None,
                 log: Optional[Callable] = None, ciclo_min: Optional[int] = None,
                 crear_historia: Optional[Callable] = None, pausa_s: float = 1.0):
        self.modo = modo or config.AVISOS_MODO
        self.store = store or Store(ruta_store(self.modo))
        self.reglas = reglas or av.Reglas.desde_config(config.AVISOS_HORARIO,
                                                       config.AVISOS_RESUMEN)
        self.h = historia
        self._crear = crear_historia or (lambda t: Historia(t - timedelta(days=VIVO_DIAS)))
        self.enviar = enviar or _enviar_telegram
        self.ahora = ahora or (lambda: datetime.now(timezone.utc))
        self.log = log or (lambda m: print(m, flush=True))
        self.ciclo_min = ciclo_min or config.AVISOS_CICLO_MIN
        self.pausa_s = pausa_s           # entre mensajes (Telegram: ~20 por minuto por grupo)
        self._t_inv: Optional[datetime] = None
        self._dia: Optional[date] = None

    def _datos(self, t: datetime) -> dict:
        if self.h is None:
            self.h = self._crear(t)
        errores = self.h.actualizar(t)
        hoy = av.lima(t).date()
        if self._dia != hoy and self.h.cargado:
            if self._dia is not None:
                self.h.recortar(t)
            self.h.calcular_cadencias(t)
            self._dia = hoy
        if self._t_inv is None:
            self._t_inv = t
        elif t - self._t_inv >= timedelta(hours=1):
            self._t_inv = t
            try:
                self.h.refrescar_inventario(t)
            except Exception as ex:  # noqa: BLE001 - se reintenta en una hora
                self.log(f"Avisos: no pude refrescar el inventario: {_error(ex)}")
        return errores

    def ciclo(self) -> list:
        t = self.ahora()
        estado = self.store.estado()
        try:
            errores = self._datos(t)
        except Exception as ex:  # noqa: BLE001 - lo inesperado se trata como base caída
            errores = {b: _error(ex) for b in BASES}
        obs = self.h.observar(t) if self.h is not None else []
        eventos = av.evaluar(estado, obs, t, self.reglas, self.store.mantenimientos(t), errores)
        msgs = av.componer(eventos, t, self.reglas)
        msgs += self._resumen(estado, t, errores, obs)
        self._entregar(msgs)
        self.store.cierres(eventos)
        self.store.guardar_estado(estado)
        if self.h is not None and self.h.unidades:
            self.store.escribir("unidades", json.dumps(
                [_u_dict(u) for u in self.h.unidades.values()], ensure_ascii=False))
        self.store.escribir("ciclo", json.dumps({
            "t": _iso(t), "modo": self.modo, "vigilados": len(obs), "ciclo_min": self.ciclo_min,
            "errores": {b: e for b, e in errores.items() if e}}, ensure_ascii=False))
        return msgs

    def _resumen(self, estado: av.Estado, t: datetime, errores: dict, obs: list) -> list:
        if self.h is None or not self.h.cargado or not av.toca_resumen(estado, t, self.reglas):
            return []
        tl = av.lima(t)
        if any(errores.values()) and tl.hour * 60 + tl.minute < self.reglas.resumen_min + 120:
            return []                    # espera hasta 2 h a que vuelva la base
        fecha = tl.date() - timedelta(days=1)
        d0 = datetime(fecha.year, fecha.month, fecha.day, tzinfo=av.LIMA)
        if "ambiental" in self.h.cargado:
            try:
                self.h.cargar_snr(d0 - timedelta(days=SNR_DIAS), t)
            except Exception as ex:  # noqa: BLE001 - el resumen sale igual, sin SNR
                self.log(f"Avisos: no pude leer el SNR: {_error(ex)}")
        return av.resumen_diario(estado, self.h.dia(fecha), fecha, t, self.reglas,
                                 self.h.sin_primera(t), self.store.mantenimientos(d0, t),
                                 [o.unidad for o in obs])

    def _entregar(self, msgs: list) -> None:
        if self.modo != "enviar":
            for m in msgs:
                self.store.registrar(m, self.modo, False)
                self.log(f"[simulación] {m.texto.splitlines()[0]}")
            return
        t = self.ahora()
        pend = [_msg_de(d) for d in json.loads(self.store.leer("pendientes") or "[]")]
        cola = []
        for m in pend:
            if t - m.t <= timedelta(hours=PENDIENTE_MAX_H):
                cola.append(m)
            else:
                self.store.registrar(m, self.modo, False, "no se pudo enviar en 6 h")
        cola += msgs
        quedan = []
        for m in cola:
            if quedan:                   # sin red: el resto espera al próximo ciclo
                quedan.append(m)
                continue
            try:
                self.enviar(m.texto, m.silencioso)
            except Exception as ex:  # noqa: BLE001 - se reintenta
                self.log(f"Avisos: no pude enviar ({_error(ex)}); reintento en el próximo ciclo")
                quedan.append(m)
            else:
                self.store.registrar(m, self.modo, True)
                if self.pausa_s:
                    time.sleep(self.pausa_s)
        self.store.escribir("pendientes", json.dumps([_msg_dict(m) for m in quedan],
                                                     ensure_ascii=False))

    def correr(self, parar: threading.Event) -> None:
        self.log(f"Avisos automáticos {MODO_TXT.get(self.modo, self.modo)}: cada "
                 f"{self.ciclo_min} min (estado en {self.store.ruta})")
        while not parar.is_set():
            inicio = time.monotonic()
            try:
                msgs = self.ciclo()
                if msgs:
                    self.log(f"Avisos: {len(msgs)} mensaje(s) ({self.modo})")
            except Exception as ex:  # noqa: BLE001 - el hilo no debe morir
                self.log(f"Avisos: el ciclo falló: {_error(ex)}")
            parar.wait(max(5.0, self.ciclo_min * 60 - (time.monotonic() - inicio)))


def iniciar(log: Optional[Callable] = None) -> Optional[threading.Event]:
    """Arranca el motor en un hilo (si AVISOS_MODO no es 'apagado'). Devuelve su freno."""
    if config.AVISOS_MODO == "apagado":
        return None
    motor = Motor(log=log)
    parar = threading.Event()
    threading.Thread(target=motor.correr, args=(parar,), name="avisos", daemon=True).start()
    return parar


# ---------- Comandos del bot: /avisos y /mantenimiento --------------------------------

def _store_de(modo: str, store: Optional[Store]) -> Store:
    return store or Store(ruta_store(modo))


def texto_estado(store: Optional[Store] = None, modo: Optional[str] = None,
                 t: Optional[datetime] = None) -> str:
    """Respuesta de /avisos: modo, último ciclo, lo abierto, mantenimientos y últimos avisos."""
    modo = modo or config.AVISOS_MODO
    if modo == "apagado":
        return "🔕 Los avisos automáticos están apagados (AVISOS_MODO=apagado en .env)."
    store, t = _store_de(modo, store), t or datetime.now(timezone.utc)
    r = av.Reglas.desde_config(config.AVISOS_HORARIO, config.AVISOS_RESUMEN)
    lineas = [f"🔔 Avisos automáticos {MODO_TXT.get(modo, modo)}"]
    if modo == "simular":
        lineas.append("Abajo va lo que se habría avisado. Para activarlos: AVISOS_MODO=enviar "
                      "en .env y reiniciar el bot.")
    ciclo = json.loads(store.leer("ciclo") or "{}")
    if ciclo:
        lineas.append(f"Último ciclo: {av.cuando(datetime.fromisoformat(ciclo['t']), t)} · "
                      f"vigilando {ciclo.get('vigilados', 0)} · cada {ciclo.get('ciclo_min')} min")
        for base, err in (ciclo.get("errores") or {}).items():
            lineas.append(f"⚠️ No pude leer {av.NOMBRE_BASE.get(base, base)}: {err}")
    else:
        lineas.append("Todavía no corrió ningún ciclo.")
    lineas.append(f"Horario de los 🟠: {r.horario_ini // 60:02d}:{r.horario_ini % 60:02d}–"
                  f"{r.horario_fin // 60:02d}:{r.horario_fin % 60:02d} · resumen 🟡 a las "
                  f"{r.resumen_min // 60:02d}:{r.resumen_min % 60:02d}")
    estado = store.estado()
    abiertos = av.lineas_abiertos(estado, t)
    n = sum(1 for i in estado.abiertos.values() if not av.cubierto(i, estado.abiertos))
    lineas += [""] + ([f"Abiertos ({n}):"] + abiertos if abiertos else ["✅ Nada abierto."])
    mants = store.mantenimientos(t)
    if mants:
        lineas += ["", "🛠 En mantenimiento:"] + [
            f"• {m.alcance} hasta {av.cuando(m.hasta, t)}" + (f" ({m.por})" if m.por else "")
            for m in mants]
    ultimos = store.ultimos(5)
    if ultimos:
        lineas += ["", "Últimos avisos" + (" simulados:" if modo == "simular" else ":")]
        for f in ultimos:
            primera = (f["texto"].splitlines() or [""])[0][:120]
            lineas.append(f"• {av.cuando(datetime.fromisoformat(f['t']), t)} · {primera}")
    return "\n".join(lineas)


def duracion_min(texto: str) -> Optional[int]:
    """'2h' → 120, '90m' → 90, '1d' → 1440, '2h30m' → 150, '45' → 45; si no, None."""
    s = (texto or "").strip().lower().replace(" ", "")
    if s.isdigit():
        return int(s)
    m = DURACION.match(s)
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mi


def _unidades_guardadas(store: Store) -> list:
    try:
        datos = json.loads(store.leer("unidades") or "[]")
    except ValueError:
        return []
    return [av.Unidad(**d) for d in datos]


def comando_mantenimiento(args: str, por: str = "", store: Optional[Store] = None,
                          modo: Optional[str] = None, t: Optional[datetime] = None) -> str:
    """/mantenimiento <qué> [duración] · /mantenimiento fin [qué] · /mantenimiento (lista)."""
    modo = modo or config.AVISOS_MODO
    if modo == "apagado":
        return "🔕 Los avisos automáticos están apagados: no hace falta mantenimiento."
    store, t = _store_de(modo, store), t or datetime.now(timezone.utc)
    partes = (args or "").split()
    uso = ("Uso: /mantenimiento <empresa, sede, gateway, sala o equipo> [duración]\n"
           "Ej.: /mantenimiento Gateway 2 3h · /mantenimiento todo 30m · "
           "/mantenimiento fin (termina todos) · /mantenimiento fin Sanna")
    if not partes:
        mants = store.mantenimientos(t)
        if not mants:
            return "No hay mantenimientos en curso.\n" + uso
        return "\n".join(["🛠 En mantenimiento:"] + [
            f"• {m.alcance} hasta {av.cuando(m.hasta, t)}" + (f" ({m.por})" if m.por else "")
            for m in mants])
    if partes[0].lower() in FIN:
        hechos = store.terminar_mantenimientos(t, " ".join(partes[1:]) or None)
        if not hechos:
            return "No había mantenimientos en curso" + (
                f" para «{' '.join(partes[1:])}»." if partes[1:] else ".")
        return ("✅ Terminé el mantenimiento de " + ", ".join(f"«{m.alcance}»" for m in hechos)
                + ". Lo que siga mal se avisa en el próximo ciclo.")
    minutos = duracion_min(partes[-1]) if len(partes) > 1 else None
    if minutos is not None:
        partes = partes[:-1]
    minutos = MANT_DEFECTO_MIN if minutos is None else minutos
    if not 1 <= minutos <= MAX_MANT_MIN:
        return "La duración va de 1 minuto a 7 días (ej.: 30m, 2h, 1d)."
    alcance = " ".join(partes).strip()
    unidades = av.unidades_en(alcance, _unidades_guardadas(store))
    todo = av.norm(alcance) in av.TODO
    if not unidades and not todo:
        return (f"No encontré nada con «{alcance}». Prueba con el nombre de la empresa, la "
                "sede, el gateway, la sala o el equipo (ej.: Sanna, Gateway 2, Zona Verde).\n"
                + uso)
    m = store.poner_mantenimiento(alcance, t, t + timedelta(minutes=minutos), por=por)
    if todo:
        que = "todo"
    else:
        nombres = ", ".join(u.nombre for u in unidades[:5])
        que = f"{len(unidades)} ({nombres}{', …' if len(unidades) > 5 else ''})"
    return (f"🛠 Mantenimiento hasta {av.cuando(m.hasta, t)} ({av.duracion(minutos)}) para "
            f"«{alcance}»: {que}. No se avisa nada de eso; lo que siga mal al terminar se "
            "avisa entonces. Para terminarlo antes: /mantenimiento fin")


# ---------- Backtest ------------------------------------------------------------------

def backtest(desde: date, hasta: date, reglas: Optional[av.Reglas] = None, paso_min: int = 5,
             consultar: Optional[Callable] = None) -> dict:
    """Corre el motor sobre lo que pasó entre `desde` y `hasta` (días Lima, incluidos).

    Carga además los 7 días previos (cadencias y referencias). El inventario es
    el de hoy. Devuelve mensajes, eventos y el estado final.
    """
    r = reglas or av.Reglas.desde_config(config.AVISOS_HORARIO, config.AVISOS_RESUMEN)
    ini = datetime(desde.year, desde.month, desde.day, tzinfo=av.LIMA)
    fin = datetime(hasta.year, hasta.month, hasta.day, tzinfo=av.LIMA) + timedelta(days=1)
    h = Historia(ini - timedelta(days=7), consultar)
    reloj = time.monotonic()
    errores = h.actualizar(fin)
    if any(errores.values()):
        raise RuntimeError("No se pudo cargar: " + "; ".join(
            f"{b}: {e}" for b, e in errores.items() if e))
    carga_s = time.monotonic() - reloj
    estado, mensajes, eventos = av.Estado(), [], []
    t, dia, paso = ini, None, timedelta(minutes=paso_min)
    while t < fin:
        if av.lima(t).date() != dia:
            h.calcular_cadencias(t)
            dia = av.lima(t).date()
        obs = h.observar(t)
        evs = av.evaluar(estado, obs, t, r)
        ms = av.componer(evs, t, r)
        if av.toca_resumen(estado, t, r):
            fecha = av.lima(t).date() - timedelta(days=1)
            ms += av.resumen_diario(estado, h.dia(fecha), fecha, t, r, h.sin_primera(t),
                                    (), [o.unidad for o in obs])
        mensajes += ms
        eventos += evs
        t += paso
    # Resumen del último día, a la hora del resumen siguiente (sin evaluar más allá de fin).
    obs = h.observar(fin)
    mensajes += av.resumen_diario(estado, h.dia(hasta), hasta,
                                  fin + timedelta(minutes=r.resumen_min), r,
                                  h.sin_primera(fin), (), [o.unidad for o in obs])
    return {"mensajes": mensajes, "eventos": eventos, "estado": estado, "historia": h,
            "reglas": r, "carga_s": carga_s, "total_s": time.monotonic() - reloj}
