"""Datos para el motor de avisos (src/avisos.py): historia en memoria por unidad.

Lee producción en SOLO LECTURA (src/db.py: sesión read-only + validate_sql) y
guarda en memoria, minuto a minuto, cuándo llegó alguna lectura de cada
unidad. Con eso responde lo que el motor pregunta en cualquier instante t
(última lectura, si va estable, desde cuándo volvió…), igual en vivo que en
el backtest de semanas pasadas.

Qué se guarda:
- presencia por minuto (bytearray) de cada unidad y de cada indicador
  ambiental: energía se trae agregada por hora como máscara de 60 bits (en
  28 días son 1.2 M minutos → ~21 k filas);
- contador de energía por hora (sin las lecturas en 0, que son fallas
  conocidas de los ADW210): mínimo, máximo, mayor kWh/min y pasos;
- corridas de valores iguales seguidos (ambiental: valor fijo; agua: estado);
- agua: litros, estado de batería y SNR de cada lectura;
- batería ambiental (historial de uplinks: solo las claves battery y devEUI;
  el JSON trae también la contraseña del equipo y NO se lee) y SNR diario.

Unidades: energía = punto (`energia:<id>`), ambiental = sensor de una sala
(`ambiental:<device_id>`, sus indicadores son `ambiental:<device>:<id>`),
agua = medidor (`agua:<id>`; se excluyen los virtuales `dependency`).
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from . import avisos as av
from .foto_salas import cadencia_observada, cortes as tramos_sin_datos, minutos_sin_datos
from .gateways import gateway_de
from .monitor import EXCLUIR

DIAS_ACTIVO = 30          # sin lecturas en 30 días = no se vigila (como /energia)
VIVO_DIAS = 15            # historia que se guarda en vivo (cadencias y referencias: 14 días)
SNR_DIAS = 15             # días de SNR que se cargan (referencia = 14 días previos)
CARGA_DIAS = 7            # la carga inicial va por tramos de 7 días (energía: ~10 s c/u)
BASES = ("energia", "ambiental")
MODULOS_DE = {"energia": ("energia", "agua"), "ambiental": ("ambiental",)}
EPS_KWH = 1e-6
EPS_L = 0.5
# Rangos físicos: fuera de esto el valor es imposible (falla del sensor).
RANGOS = {"CO2": (250, 10000, "ppm"), "TEMPERATURE": (-20, 60, "°C"),
          "HUMIDITY": (0, 100, "%")}

SQL = {
    # Ojo con los alias: Row de SQLAlchemy ya tiene .t, .tuple, .count e .index
    # (r.t devuelve la fila, no la columna "t"). Por eso el instante se llama ts.
    "inv_energia": """
        SELECT e.name AS empresa, eh.name AS sede, ep.name AS tablero, d.dev_eui AS eui,
               mp.id AS uid, mp.name AS nombre, mp.created_at AS alta
        FROM enterprises_measurementpoint mp
        JOIN devices_device d ON d.id = mp.device_id
        JOIN enterprises_electricalpanel ep ON ep.id = d.electrical_panel_id
        JOIN enterprises_energyheadquarter eh ON eh.id = ep.energy_headquarter_id
        JOIN enterprises_enterprise e ON e.id = eh.enterprise_id
        WHERE mp.is_active""",
    "inv_ambiental": """
        SELECT f.name AS empresa, hq.name AS sede, r.id AS sala_id, r.name AS sala,
               d.id AS device_id, d.dev_eui AS eui, d.type_sensor AS modelo,
               rd.id AS uid, i.name AS indicador
        FROM equipments_indicatordevice rd
        JOIN equipments_indicator i ON i.id = rd.indicator_id
        JOIN equipments_device d ON d.id = rd.device_id
        JOIN enterprise_room r ON r.id = d.room_id
        JOIN enterprise_headquarters hq ON hq.id = r.headquarter_id
        JOIN enterprise_enterprise f ON f.id = hq.enterprise_id
        WHERE rd.is_activated AND d.is_activated""",
    # El medidor guarda dev_eui_password (credencial): no se lee.
    "inv_agua": """
        SELECT e.name AS empresa, eh.name AS sede, wp.name AS tuberia, mw.id AS uid,
               mw.name AS nombre, mw.created_at AS alta
        FROM enterprises_measurementpointwater mw
        JOIN enterprises_waterpipe wp ON wp.id = mw.water_pipe_id
        JOIN enterprises_energyheadquarter eh ON eh.id = wp.energy_headquarter_id
        JOIN enterprises_enterprise e ON e.id = eh.enterprise_id
        WHERE mw.is_active AND NOT coalesce(mw.dependency, false)""",
    # Presencia: una fila por punto y hora con una máscara de los minutos con lectura.
    "pres_energia": """
        SELECT measurement_point_id AS uid, date_trunc('hour', created_at) AS h,
               bit_or((1::bigint) << EXTRACT(MINUTE FROM created_at)::int) AS mask
        FROM readings_reading
        WHERE measurement_point_id = ANY(:ids) AND created_at >= :ini AND created_at < :fin
          AND "EPpos_value" IS NOT NULL
        GROUP BY 1, 2""",
    # Contador por hora sin las lecturas en 0 (fallas de los ADW210): el paso
    # se normaliza por minutos (tras un hueco el contador salta lo acumulado).
    "cont_energia": """
        WITH x AS (
            SELECT measurement_point_id AS uid, created_at, "EPpos_value" AS ep,
                   "EPpos_value" - lag("EPpos_value") OVER w AS paso,
                   extract(epoch FROM created_at - lag(created_at) OVER w) / 60.0 AS minutos
            FROM readings_reading
            WHERE measurement_point_id = ANY(:ids) AND created_at >= :ini AND created_at < :fin
              AND "EPpos_value" > 0
            WINDOW w AS (PARTITION BY measurement_point_id ORDER BY created_at))
        SELECT uid, date_trunc('hour', created_at) AS h, min(ep) AS ep_min, max(ep) AS ep_max,
               max(paso / greatest(minutos, 1)) AS tasa_max,
               min(paso) AS paso_min, max(paso) AS paso_max
        FROM x GROUP BY 1, 2""",
    "lect_ambiental": """
        SELECT indicator_device_id AS uid, created_at AS ts, value AS v
        FROM readings_reading
        WHERE room_id = ANY(:salas) AND indicator_device_id = ANY(:ids)
          AND created_at >= :ini AND created_at < :fin
        ORDER BY 1, 2""",
    "lect_agua": """
        SELECT measurement_point_water_id AS uid, created_at AS ts, consumo_litros AS litros,
               estado_bateria, estado_medicion, estado_tuberia, snr
        FROM readings_readingwater
        WHERE measurement_point_water_id = ANY(:ids) AND created_at >= :ini AND created_at < :fin
          AND consumo_litros IS NOT NULL
        ORDER BY 1, 2""",
    # Historial de uplinks: solo battery/snr/devEUI (el JSON trae la contraseña).
    "bat_ambiental": """
        SELECT h.room_id AS sala_id, lower(h.data_json->>'devEUI') AS eui, h.created_at AS ts,
               (h.data_json->>'battery')::numeric AS bateria
        FROM historical_readinghistory h
        WHERE h.room_id = ANY(:salas) AND h.created_at >= :ini AND h.created_at < :fin
          AND jsonb_typeof(h.data_json->'battery') = 'number'""",
    "bat_ambiental_ahora": """
        SELECT d.id AS device_id, d.battery AS bateria
        FROM equipments_device d
        WHERE d.id = ANY(:ids) AND d.battery IS NOT NULL""",
    "snr_ambiental": """
        SELECT h.room_id AS sala_id, lower(h.data_json->>'devEUI') AS eui,
               (h.created_at AT TIME ZONE 'America/Lima')::date AS dia,
               avg((h.data_json->>'snr')::numeric) AS snr
        FROM historical_readinghistory h
        WHERE h.room_id = ANY(:salas) AND h.created_at >= :ini AND h.created_at < :fin
          AND jsonb_typeof(h.data_json->'snr') = 'number'
        GROUP BY 1, 2, 3""",
    "ult_energia": """
        SELECT measurement_point_id AS uid, max(created_at) AS ult FROM readings_reading
        WHERE measurement_point_id = ANY(:ids) AND created_at >= :desde AND created_at < :hasta
          AND "EPpos_value" IS NOT NULL
        GROUP BY 1""",
    "ult_ambiental": """
        SELECT indicator_device_id AS uid, max(created_at) AS ult FROM readings_reading
        WHERE indicator_device_id = ANY(:ids) AND created_at >= :desde AND created_at < :hasta
        GROUP BY 1""",
    "ult_agua": """
        SELECT measurement_point_water_id AS uid, max(created_at) AS ult FROM readings_readingwater
        WHERE measurement_point_water_id = ANY(:ids) AND created_at >= :desde
          AND created_at < :hasta AND consumo_litros IS NOT NULL
        GROUP BY 1""",
    # ¿Alguna vez reportó? (por índice: instantáneo)
    "alguna_energia": """
        SELECT x.id AS uid FROM unnest(CAST(:ids AS bigint[])) AS x(id)
        WHERE EXISTS (SELECT 1 FROM readings_reading r WHERE r.measurement_point_id = x.id)""",
    "alguna_ambiental": """
        SELECT x.id AS uid FROM unnest(CAST(:ids AS bigint[])) AS x(id)
        WHERE EXISTS (SELECT 1 FROM readings_reading r WHERE r.indicator_device_id = x.id)""",
    "alguna_agua": """
        SELECT x.id AS uid FROM unnest(CAST(:ids AS bigint[])) AS x(id)
        WHERE EXISTS (SELECT 1 FROM readings_readingwater r
                      WHERE r.measurement_point_water_id = x.id)""",
}


def consultar_db(nombre: str, base: str, sql: str, params: dict) -> list:
    """Consulta de solo lectura en producción (o la base configurada en .env)."""
    from sqlalchemy import text

    from .db import get_engine, validate_sql
    with get_engine(base).connect() as conn:
        return conn.execute(text(validate_sql(sql)), params).fetchall()


def _minuto(t: datetime) -> datetime:
    return t.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _excluida(empresa) -> bool:
    return (empresa or "").strip().lower() in EXCLUIR


def _error(ex: Exception) -> str:
    txt = (str(ex).strip().splitlines() or [""])[0]
    return f"{type(ex).__name__}: {txt}"[:180]


def estado_agua(tuberia, medicion) -> str:
    """'' si el medidor está normal; si no, qué reporta ('tubería vacia')."""
    partes = []
    if tuberia and av.norm(tuberia) != "normal":
        partes.append(f"tubería {tuberia}")
    if medicion and av.norm(medicion) != "correcta":
        partes.append(f"medición {medicion}")
    return " y ".join(partes)


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def referencia(valores: list) -> Optional[float]:
    """Lo normal de un equipo: el percentil 75 de sus días previos (mínimo 3).

    Con el promedio, una degradación de varios días se vuelve "normal" (el SNR
    de Zona Roja dejó de verse como malo a los 4 días); el percentil 75 de
    14 días aguanta hasta ~10 días malos.
    """
    if len(valores) < 3:
        return None
    v = sorted(valores)
    return v[min(len(v) - 1, math.ceil(0.75 * len(v)) - 1)]


class ObsHistoria(av.Obs):
    """Obs que responde con la presencia minuto a minuto de la Historia."""

    def __init__(self, historia: "Historia", unidad: av.Unidad, ultima: Optional[datetime]):
        super().__init__(unidad, ultima=ultima)
        self._h = historia

    def primera_desde(self, t0: datetime, t: datetime) -> Optional[datetime]:
        return self._h.primera_desde(self.unidad.clave, t0, t)

    def estable(self, t: datetime, minutos: int, hueco: Optional[int] = None) -> bool:
        return self._h.estable(self.unidad.clave, t, minutos, hueco)

    def regreso(self, t: datetime, desde: datetime, hueco: Optional[int] = None) -> Optional[datetime]:
        return self._h.regreso(self.unidad.clave, t, desde, hueco)


class Historia:
    """Lecturas de todas las unidades desde `ini` (en memoria)."""

    def __init__(self, ini: datetime, consultar: Optional[Callable] = None):
        self.t0 = _minuto(ini)
        self.n = 0                      # minutos guardados en cada bytearray
        self.consultar = consultar or consultar_db
        self.unidades: dict = {}        # clave → av.Unidad
        self.series: dict = {}          # unidad → [series] (ambiental: sus indicadores)
        self.indicador: dict = {}       # serie ambiental → CO2 | TEMPERATURE | HUMIDITY…
        self.serie_rd: dict = {}        # id de indicator_device → serie
        self.sala: dict = {}            # unidad ambiental → sala_id
        self.eui: dict = {}             # unidad ambiental → devEUI (minúsculas)
        self.alta: dict = {}            # unidad → alta en el inventario (energía/agua)
        self.pres: dict = {}            # unidad o serie → bytearray (1 = hubo lectura)
        self.contador: dict = {}        # unidad energía → {hora: (min, max, tasa, paso_min, paso_max)}
        self.corridas: dict = {}        # serie → [[t_ini, t_fin, valor]] valores iguales seguidos
        self.agua: dict = {}            # unidad agua → [(t, litros, estado_bateria, snr)]
        self.baterias: dict = {}        # unidad ambiental → [(t, %)]
        self.snr: dict = {}             # unidad ambiental → {fecha: snr promedio}
        self.previa: dict = {}          # unidad → última lectura antes de t0 (≤ 30 días)
        self.instalada: dict = {}       # unidad → primera lectura, si empezó después de t0
        self.nunca: dict = {}           # unidad → alta (activa pero jamás reportó)
        self.cargado: dict = {}         # base → hasta dónde se cargó
        self._revisadas: set = set()
        self._aumentos: dict = {}
        self._inicios: dict = {}
        self._prefijos: dict = {}       # unidad → (n, H0, horas con datos acumuladas)
        self._refs: dict = {}           # (unidad, nº de subida) → (n, quietud de referencia)

    # ---------- índices y presencia ----------

    def _q(self, nombre: str, base: str, **params) -> list:
        return self.consultar(nombre, base, SQL[nombre], params)

    def _idx(self, t: datetime) -> int:
        return int((t - self.t0).total_seconds() // 60)

    def _t(self, i: int) -> datetime:
        return self.t0 + timedelta(minutes=i)

    def _ba(self, clave: str) -> bytearray:
        ba = self.pres.get(clave)
        if ba is None:
            ba = self.pres[clave] = bytearray(self.n)
        return ba

    def _extender(self, fin: datetime) -> None:
        n = max(self.n, self._idx(fin) + 1)
        for ba in self.pres.values():
            if len(ba) < n:
                ba.extend(bytes(n - len(ba)))
        self.n = n

    def _marcar(self, clave: str, t: datetime) -> None:
        ba = self.pres.get(clave)
        i = self._idx(t)
        if ba is not None and 0 <= i < len(ba):
            ba[i] = 1

    def _correr(self, serie: str, t: datetime, valor) -> None:
        runs = self.corridas.setdefault(serie, [])
        if runs and runs[-1][2] == valor:
            runs[-1][1] = t
        else:
            runs.append([t, t, valor])
            self._inicios.pop(serie, None)

    def _inicios_de(self, serie: str) -> list:
        ini = self._inicios.get(serie)
        if ini is None:
            ini = self._inicios[serie] = [r[0] for r in self.corridas.get(serie, [])]
        return ini

    # ---------- carga ----------

    def _inventario(self, base: str) -> None:
        mods = MODULOS_DE[base]
        unidades = {k: u for k, u in self.unidades.items() if u.modulo not in mods}
        series = {k: s for k, s in self.series.items() if k in unidades}
        if base == "energia":
            for r in self._q("inv_energia", base):
                if _excluida(r.empresa):
                    continue
                k = f"energia:{r.uid}"
                unidades[k] = av.Unidad(k, "energia", r.nombre or f"Punto {r.uid}",
                                        r.tablero or "", (r.empresa or "").strip(),
                                        (r.sede or "").strip(), gateway_de(r.eui), 1)
                series[k] = [k]
                self.alta[k] = r.alta
            for r in self._q("inv_agua", base):
                if _excluida(r.empresa):
                    continue
                k = f"agua:{r.uid}"
                unidades[k] = av.Unidad(k, "agua", r.tuberia or f"Medidor {r.uid}",
                                        r.nombre or "", (r.empresa or "").strip(),
                                        (r.sede or "").strip(), None, 10)
                series[k] = [k]
                self.alta[k] = r.alta
        else:
            filas = [r for r in self._q("inv_ambiental", base) if not _excluida(r.empresa)]
            por_sala = Counter(s for s, _ in {(r.sala_id, r.device_id) for r in filas})
            for r in filas:
                k = f"ambiental:{r.device_id}"
                if k not in unidades:
                    nombre = r.sala or f"Sala {r.sala_id}"
                    if por_sala[r.sala_id] > 1:
                        nombre += f" ({r.modelo})"
                    unidades[k] = av.Unidad(k, "ambiental", nombre, "",
                                            (r.empresa or "").strip(), (r.sede or "").strip(),
                                            gateway_de(r.eui), 5)
                    series[k] = []
                    self.sala[k] = r.sala_id
                    self.eui[k] = (r.eui or "").strip().lower()
                s = f"{k}:{r.uid}"
                if s not in series[k]:
                    series[k].append(s)
                self.indicador[s] = (r.indicador or "").strip().upper()
                self.serie_rd[r.uid] = s
        for k, u in unidades.items():           # la cadencia se conserva entre recargas
            if k in self.unidades:
                u.cadencia = self.unidades[k].cadencia
        self.unidades, self.series = unidades, series
        for k, ss in series.items():
            self._ba(k)
            for s in ss:
                self._ba(s)

    def _ids(self, modulo: str) -> list:
        return [int(k.split(":")[1]) for k, u in self.unidades.items() if u.modulo == modulo]

    def _cargar(self, base: str, ini: datetime, fin: datetime) -> None:
        ini = max(ini, self.t0)
        if base == "ambiental":
            self._cargar_ambiental(ini, fin)
            return
        ids = self._ids("energia")
        if ids:
            for r in self._q("pres_energia", base, ids=ids, ini=ini, fin=fin):
                ba = self.pres.get(f"energia:{r.uid}")
                if ba is None:
                    continue
                i0, mask = self._idx(r.h), int(r.mask)
                while mask:
                    bajo = mask & -mask
                    i = i0 + bajo.bit_length() - 1
                    if 0 <= i < len(ba):
                        ba[i] = 1
                    mask ^= bajo
            # Desde la hora anterior: así cada hora guardada trae el paso desde la previa.
            h_ini = int(ini.timestamp() // 3600)
            desde = datetime.fromtimestamp((h_ini - 1) * 3600, timezone.utc)
            for r in self._q("cont_energia", base, ids=ids, ini=desde, fin=fin):
                h = int(r.h.timestamp() // 3600)
                if h < h_ini or r.ep_max is None:
                    continue
                k = f"energia:{r.uid}"
                self.contador.setdefault(k, {})[h] = (
                    float(r.ep_min), float(r.ep_max), float(r.tasa_max or 0),
                    float(r.paso_min or 0), float(r.paso_max or 0))
                self._aumentos.pop(k, None)
        ids = self._ids("agua")
        if ids:
            for r in self._q("lect_agua", base, ids=ids, ini=ini, fin=fin):
                k = f"agua:{r.uid}"
                lista = self.agua.setdefault(k, [])
                if lista and r.ts <= lista[-1][0]:
                    continue                    # ya estaba (la recarga se solapa 3 min)
                self._marcar(k, r.ts)
                lista.append((r.ts, float(r.litros), r.estado_bateria, _num(r.snr)))
                self._correr(k, r.ts, estado_agua(r.estado_tuberia, r.estado_medicion))
                self._aumentos.pop(k, None)

    def _cargar_ambiental(self, ini: datetime, fin: datetime) -> None:
        ids, salas = sorted(self.serie_rd), sorted(set(self.sala.values()))
        if not ids:
            return
        for r in self._q("lect_ambiental", "ambiental", ids=ids, salas=salas, ini=ini, fin=fin):
            s = self.serie_rd.get(r.uid)
            if s is None:
                continue
            runs = self.corridas.get(s)
            if runs and r.ts <= runs[-1][1]:
                continue                        # ya estaba (la recarga se solapa 3 min)
            self._marcar(s, r.ts)
            self._marcar(s.rsplit(":", 1)[0], r.ts)
            self._correr(s, r.ts, r.v)

    def _unidad_amb(self, sala_id, eui) -> Optional[str]:
        en_sala = [k for k, s in self.sala.items() if s == sala_id]
        por_eui = [k for k in en_sala if self.eui.get(k) == (eui or "").lower()]
        if por_eui:
            return por_eui[0]
        return en_sala[0] if len(en_sala) == 1 else None

    def cargar_baterias(self, ini: datetime, fin: datetime) -> None:
        """Batería ambiental desde el historial de uplinks (para el pasado)."""
        salas = sorted(set(self.sala.values()))
        if not salas:
            return
        for r in self._q("bat_ambiental", "ambiental", salas=salas, ini=ini, fin=fin):
            k = self._unidad_amb(r.sala_id, r.eui)
            if k and r.bateria is not None:
                self.baterias.setdefault(k, []).append((r.ts, float(r.bateria)))
        for lista in self.baterias.values():
            lista.sort()

    def refrescar_baterias(self, t: datetime) -> None:
        """Batería actual (equipments_device.battery = la del último uplink)."""
        ids = [int(k.split(":")[1]) for k in self.sala]
        if not ids:
            return
        for r in self._q("bat_ambiental_ahora", "ambiental", ids=ids):
            k = f"ambiental:{r.device_id}"
            lista = self.baterias.setdefault(k, [])
            v = float(r.bateria)
            if not lista or lista[-1][1] != v or t - lista[-1][0] > timedelta(hours=6):
                lista.append((t, v))

    def cargar_snr(self, ini: datetime, fin: datetime) -> None:
        salas = sorted(set(self.sala.values()))
        if not salas:
            return
        for r in self._q("snr_ambiental", "ambiental", salas=salas, ini=ini, fin=fin):
            k = self._unidad_amb(r.sala_id, r.eui)
            if k and r.snr is not None:
                self.snr.setdefault(k, {})[r.dia] = float(r.snr)

    def _completar(self, base: str, t: datetime) -> None:
        """Última lectura previa a t0 (≤30 días), quién nunca reportó y quién es nuevo."""
        pend = [k for k, u in self.unidades.items()
                if u.modulo in MODULOS_DE[base] and k not in self._revisadas]
        primera = {k: self.pres[k].find(1) for k in pend}
        # Sin datos al inicio de lo cargado: ¿reportaba antes (corte) o es nuevo?
        tardias = [k for k in pend if primera[k] < 0 or primera[k] > 60]
        desde = self.t0 - timedelta(days=DIAS_ACTIVO)
        for mod in MODULOS_DE[base]:
            ks = [k for k in tardias if self.unidades[k].modulo == mod]
            if not ks:
                continue
            if mod == "ambiental":
                rd = {int(s.rsplit(":", 1)[1]): k for k in ks for s in self.series[k]}
            else:
                rd = {int(k.split(":")[1]): k for k in ks}
            if not rd:
                continue
            for r in self._q(f"ult_{mod}", base, ids=sorted(rd), desde=desde, hasta=self.t0):
                k = rd.get(r.uid)
                if k and r.ult is not None and (k not in self.previa or r.ult > self.previa[k]):
                    self.previa[k] = r.ult
            faltan = {i: k for i, k in rd.items() if k not in self.previa and primera[k] < 0}
            if faltan:
                alguna = {rd[r.uid] for r in self._q(f"alguna_{mod}", base, ids=sorted(faltan))}
                for k in set(faltan.values()) - alguna:
                    self.nunca[k] = self.alta.get(k)
        for k in tardias:
            if k not in self.previa and primera[k] > 60:
                self.instalada[k] = self._t(primera[k])
        self._revisadas.update(pend)

    def actualizar(self, t: datetime) -> dict:
        """Carga lo que falte hasta t, base por base. Devuelve {base: error | None}.

        La primera vez trae inventario + lecturas desde t0 (+ batería y SNR del
        historial); después, solo lo nuevo (con 3 min de solape).
        """
        self._extender(t)
        errores: dict = {}
        for base in BASES:
            try:
                if base not in self.cargado:
                    self._inventario(base)
                    self._extender(t)
                    a = self.t0
                    while a < t:            # por tramos: cada consulta tarda segundos
                        b = min(t, a + timedelta(days=CARGA_DIAS))
                        self._cargar(base, a, b)
                        a = b
                    self._completar(base, t)
                    if base == "ambiental":
                        self.cargar_baterias(self.t0, t)
                        self.cargar_snr(self.t0 - timedelta(days=SNR_DIAS), t)
                else:
                    self._cargar(base, self.cargado[base] - timedelta(minutes=3), t)
            except Exception as ex:  # noqa: BLE001 - se informa como base caída
                errores[base] = _error(ex)
            else:
                self.cargado[base] = t
                errores[base] = None
        return errores

    def refrescar_inventario(self, t: datetime) -> None:
        """Altas/bajas del inventario y batería actual (en vivo, cada hora)."""
        for base in list(self.cargado):
            self._inventario(base)
            self._extender(t)
            self._completar(base, t)
        if "ambiental" in self.cargado:
            self.refrescar_baterias(t)

    def calcular_cadencias(self, t: datetime, dias: int = 14) -> None:
        """Cadencia nominal = la MENOR de las modas diarias de sus últimos 14 días.

        La moda de toda la semana se corre cuando un equipo pasa días perdiendo
        paquetes (Zona Verde, 06–15 oct: de 5 a 10 min, y escondía sus cortes);
        un solo día normal en las 2 semanas basta para saber su ritmo real.
        """
        i1 = max(0, min(self._idx(t), self.n))
        for k, u in self.unidades.items():
            if u.modulo == "energia":
                u.cadencia = 1
                continue
            ba = self.pres.get(k)
            if ba is None:
                continue
            modas = []
            for d in range(dias):
                b = i1 - d * 1440
                if b <= 0:
                    break
                a = max(0, b - 1440)
                minutos = [a + j for j, x in enumerate(ba[a:b]) if x]
                if len(minutos) >= 24:
                    modas.append(cadencia_observada(minutos, u.cadencia or 5))
            if modas:
                u.cadencia = min(modas)

    def recortar(self, t: datetime, dias: int = VIVO_DIAS) -> None:
        """Suelta lo anterior a `dias` (en vivo, una vez al día)."""
        k = self._idx(_minuto(t - timedelta(days=dias)))
        if k < 1440:
            return
        for clave, ba in self.pres.items():   # quien queda sin datos guarda su última lectura
            if clave in self.unidades and ba.find(1, k) < 0:
                j = ba.rfind(1, 0, k)
                if j >= 0:
                    self.previa[clave] = self._t(j)
            del ba[:k]
        self.t0 += timedelta(minutes=k)
        self.n = max(0, self.n - k)
        corte = self.t0
        self.corridas = {s: [r for r in runs if r[1] >= corte] for s, runs in self.corridas.items()}
        self.agua = {u: [x for x in lst if x[0] >= corte] for u, lst in self.agua.items()}
        self.baterias = {u: [x for x in lst if x[0] >= corte - timedelta(days=7)]
                         for u, lst in self.baterias.items()}
        h0 = int(corte.timestamp() // 3600)
        self.contador = {u: {h: v for h, v in d.items() if h >= h0} for u, d in self.contador.items()}
        dia0 = (corte - timedelta(days=SNR_DIAS)).astimezone(av.LIMA).date()
        self.snr = {u: {d: v for d, v in m.items() if d >= dia0} for u, m in self.snr.items()}
        self.instalada = {u: x for u, x in self.instalada.items() if x >= corte}
        self._aumentos.clear()
        self._inicios.clear()
        self._prefijos.clear()
        self._refs.clear()

    # ---------- consultas en un instante t ----------

    def ultima(self, clave: str, t: datetime) -> Optional[datetime]:
        """Última lectura ANTES del minuto de t (minuto exacto) o la previa a t0."""
        ba = self.pres.get(clave)
        if ba is not None:
            i = min(self._idx(t), len(ba))
            if i > 0:
                j = ba.rfind(1, 0, i)
                if j >= 0:
                    return self._t(j)
        return self.previa.get(clave)

    def primera_desde(self, clave: str, t0: datetime, t: datetime) -> Optional[datetime]:
        ba = self.pres.get(clave)
        if ba is None:
            return None
        j = ba.find(1, max(0, self._idx(t0) + 1), max(0, min(self._idx(t), len(ba))))
        return None if j < 0 else self._t(j)

    def estable(self, clave: str, t: datetime, minutos: int, hueco: Optional[int] = None) -> bool:
        """Sin huecos ≥ `hueco` min (por defecto, un corte) en los últimos `minutos`."""
        ba = self.pres.get(clave)
        if ba is None:
            return False
        i1 = max(0, min(self._idx(t), len(ba)))
        i0 = max(0, i1 - int(minutos))
        seg = bytes(ba[i0:i1])
        if 1 not in seg:
            return False
        z = (hueco or av.hueco_corte(self.unidades[clave].cadencia)) - 1
        return max(len(x) for x in seg.split(b"\x01")) < z

    def regreso(self, clave: str, t: datetime, desde: datetime,
                hueco: Optional[int] = None) -> Optional[datetime]:
        """Primera lectura después del último hueco ≥ `hueco` posterior a `desde`."""
        ba = self.pres.get(clave)
        if ba is None:
            return None
        z = (hueco or av.hueco_corte(self.unidades[clave].cadencia)) - 1
        i0 = max(0, self._idx(desde) + 1)
        i1 = max(0, min(self._idx(t), len(ba)))
        if i0 >= i1:
            return None
        pos = ba.rfind(b"\x00" * z, i0, i1)
        j = ba.find(1, i0 if pos < 0 else pos + z, i1)
        return None if j < 0 else self._t(j)

    def bateria(self, clave: str, t: datetime) -> Optional[float]:
        lista = self.baterias.get(clave)
        if not lista:
            return None
        j = bisect_right(lista, (t,)) - 1
        if j < 0 or t - lista[j][0] > timedelta(days=7):
            return None
        return lista[j][1]

    def fijos(self, clave: str, t: datetime) -> dict:
        """Indicador → horas que lleva marcando exactamente el mismo valor."""
        out = {}
        umbral = av.umbral_sin_datos(self.unidades[clave].cadencia)
        for s in self.series.get(clave, []):
            ult = self.ultima(s, t)
            runs = self.corridas.get(s)
            if ult is None or not runs or (t - ult).total_seconds() / 60 >= umbral:
                continue
            j = bisect_left(self._inicios_de(s), t) - 1
            if j < 0:
                continue
            ini, fin, _ = runs[j]
            fin = min(fin, ult + timedelta(minutes=1))
            if fin >= ini:
                out[self.indicador.get(s, s)] = (fin - ini).total_seconds() / 3600
        return out

    def _horas_datos(self, clave: str) -> tuple:
        """(H0, prefijo): prefijo[i] = horas con alguna lectura en [H0, H0 + i) (horas epoch)."""
        hit = self._prefijos.get(clave)
        if hit is not None and hit[0] == self.n:
            return hit[1], hit[2]
        ba = self.pres.get(clave) or bytearray()
        h0 = int(self.t0.timestamp() // 3600)
        i = -int((self.t0.timestamp() - h0 * 3600) // 60)   # t0 puede caer a media hora
        pre = [0]
        while i < len(ba):
            a, b = max(0, i), min(len(ba), i + 60)
            pre.append(pre[-1] + (1 if ba.find(1, a, b) >= 0 else 0))
            i += 60
        self._prefijos[clave] = (self.n, h0, pre)
        return h0, pre

    def _con_datos(self, clave: str, h1: int, h2: int) -> int:
        """Horas epoch en [h1, h2) en las que llegó alguna lectura."""
        h0, pre = self._horas_datos(clave)
        a = min(max(0, h1 - h0), len(pre) - 1)
        b = min(max(0, h2 - h0), len(pre) - 1)
        return max(0, pre[b] - pre[a])

    def _aumentos_de(self, clave: str) -> list:
        """[(instante en que se supo (epoch s), hora epoch)] en que el contador subió."""
        incs = self._aumentos.get(clave)
        if incs is not None:
            return incs
        incs, prev = [], None
        if clave.startswith("energia:"):
            horas = self.contador.get(clave, {})
            for h in sorted(horas):
                mn, mx = horas[h][0], horas[h][1]
                if mx - mn > EPS_KWH or (prev is not None and mn > prev + EPS_KWH):
                    incs.append(((h + 1) * 3600.0, h))   # se sabe al cerrar la hora
                prev = mx
        else:
            for x in self.agua.get(clave, []):
                if prev is not None and x[1] > prev + EPS_L:
                    e = x[0].timestamp()
                    incs.append((e, int(e // 3600)))
                prev = x[1]
        self._aumentos[clave] = incs
        return incs

    def plano(self, clave: str, t: datetime) -> tuple:
        """(horas CON lecturas sin que el contador suba, su mayor quietud en la semana
        previa, si en esas horas el contador llega en 0).

        Las horas sin lecturas no cuentan: un equipo que estuvo desconectado no
        "dejó de medir". La referencia es None si hay menos de 3 días de
        historia antes de esta quietud: sin saber qué es normal, no se avisa.
        """
        incs = self._aumentos_de(clave)
        te = t.timestamp()
        j = bisect_right(incs, (te, float("inf"))) - 1
        if j < 0:
            return None, None, False
        sabido, h_inc = incs[j]
        hc = int(te // 3600)
        ult = self.ultima(clave, t)
        actual = 1 if hc > h_inc and ult is not None and ult.timestamp() >= hc * 3600 else 0
        plano = self._con_datos(clave, h_inc + 1, hc) + actual
        cero = False
        if plano and clave.startswith("energia:"):     # llegan lecturas, pero sin contador > 0
            horas = self.contador.get(clave, {})
            cero = not any(h in horas for h in range(h_inc + 1, hc + 1))
        t0 = self.t0.timestamp()
        if t0 > sabido - 3 * 86400:
            return float(plano), None, cero
        clave_ref = (clave, j)
        hit = self._refs.get(clave_ref)
        if hit is not None and hit[0] == self.n:
            return float(plano), hit[1], cero
        ini_ref = sabido - 7 * 86400
        tramo = incs[bisect_left(incs, (ini_ref, -1)):j + 1]
        difs = [self._con_datos(clave, a[1] + 1, b[1]) for a, b in zip(tramo, tramo[1:])]
        if tramo and t0 <= ini_ref:
            difs.append(self._con_datos(clave, int(ini_ref // 3600), tramo[0][1]))
        ref = float(max([0] + difs))
        self._refs[clave_ref] = (self.n, ref)
        return float(plano), ref, cero

    def estado_agua(self, clave: str, t: datetime) -> Optional[tuple]:
        runs = self.corridas.get(clave)
        if not runs:
            return None
        j = bisect_left(self._inicios_de(clave), t) - 1
        if j < 0 or not runs[j][2]:
            return None
        ini, fin, valor = runs[j]
        ult = self.ultima(clave, t)
        fin = min(fin, (ult or fin) + timedelta(minutes=1))
        return valor, max(0.0, (fin - ini).total_seconds() / 3600)

    def observar(self, t: datetime) -> list:
        """Obs de cada unidad vigilada en t (con lecturas en los últimos 30 días)."""
        modulos = {m for b in self.cargado for m in MODULOS_DE[b]}
        lim = timedelta(days=DIAS_ACTIVO)
        out = []
        for k, u in self.unidades.items():
            if u.modulo not in modulos:
                continue
            ult = self.ultima(k, t)
            if ult is None or t - ult > lim:
                continue
            o = ObsHistoria(self, u, ult)
            fresca = (t - ult).total_seconds() / 60 < av.umbral_sin_datos(u.cadencia)
            if u.modulo == "ambiental":
                o.bateria = self.bateria(k, t)
                if fresca:
                    o.fijos = self.fijos(k, t)
            elif fresca:
                o.plano_h, o.plano_ref_h, o.contador_cero = self.plano(k, t)
                if u.modulo == "agua":
                    o.estado_agua = self.estado_agua(k, t)
            out.append(o)
        return out

    def sin_primera(self, t: datetime) -> list:
        """[(Unidad, alta)] activas hace ≥24 h que jamás enviaron una lectura."""
        out = []
        for k, alta in self.nunca.items():
            u = self.unidades.get(k)
            if u is None or self.ultima(k, t) is not None:
                continue
            if alta is not None and t - alta < timedelta(hours=24):
                continue
            out.append((u, alta))
        return sorted(out, key=lambda x: (x[0].tienda, x[0].nombre))

    # ---------- un día completo (para el resumen) ----------

    def dia(self, fecha: date) -> list:
        """[DiaUnidad] del día `fecha` (hora Lima) para lo que se vigilaba ese día."""
        d0 = datetime(fecha.year, fecha.month, fecha.day, tzinfo=av.LIMA)
        d1 = d0 + timedelta(days=1)
        i0 = self._idx(d0)
        completo = i0 >= 0 and i0 + 1440 <= self.n
        modulos = {m for b in self.cargado for m in MODULOS_DE[b]}
        out = []
        for k, u in self.unidades.items():
            if u.modulo not in modulos:
                continue
            ult = self.ultima(k, d1)
            if ult is None or d1 - ult > timedelta(days=DIAS_ACTIVO):
                continue
            inst = self.instalada.get(k)
            if inst is not None and inst >= d1:
                continue
            d = av.DiaUnidad(u)
            if completo and not (inst is not None and inst >= d0):
                self._cobertura(d, k, u, i0, d0)
            if u.modulo == "energia":
                d.raros = self._raros_energia(k, d0)
            elif u.modulo == "ambiental":
                d.raros = self._raros_ambiental(k, d0, d1)
                d.bateria = self.bateria(k, d1)
                d.snr, d.snr_ref = self._snr_amb(k, fecha)
            else:
                self._dia_agua(d, k, d0, d1)
            out.append(d)
        return out

    def _cobertura(self, d: av.DiaUnidad, k: str, u: av.Unidad, i0: int, d0: datetime) -> None:
        c = max(1, int(u.cadencia or 1))
        series = [s for s in (self.series.get(k) if u.modulo == "ambiental" else [k])
                  if self.pres[s].find(1) >= 0] or [k]
        falta, pcts, bloques = [0] * 1440, [], [[] for _ in range(4)]
        for s in series:
            seg = self.pres[s][i0:i0 + 1440]
            presentes = [j for j, b in enumerate(seg) if b]
            pcts.append(min(100.0, 100.0 * len(presentes) * c / 1440))
            f = minutos_sin_datos(presentes, 1440, c)
            falta = [a | b for a, b in zip(falta, f)]
            for b in range(4):
                bloques[b].append(min(100.0, 100.0 * seg[360 * b:360 * (b + 1)].count(1) * c / 360))
        d.cobertura = min(pcts)
        d.cortes = [(d0 + timedelta(minutes=a), m) for a, m in tramos_sin_datos(falta)]
        seg_u = self.pres[k][i0:i0 + 1440]
        peores = [min(bloques[b]) for b in range(4) if seg_u[360 * b:360 * (b + 1)].count(1)]
        d.peor_bloque = min(peores) if peores else None

    def _raros_energia(self, k: str, d0: datetime) -> list:
        horas = self.contador.get(k, {})
        h0 = int(d0.timestamp() // 3600)
        ref = [horas[h][2] for h in range(h0 - 168, h0) if h in horas]
        dia = [(h, horas[h]) for h in range(h0, h0 + 24) if h in horas]
        out = []
        if len(ref) >= 72:
            tope = max(ref)
            saltos = [(h, v) for h, v in dia if v[2] > 3 * tope and v[4] > 5]
            if saltos:
                h, v = max(saltos, key=lambda x: x[1][4])
                hl = datetime.fromtimestamp(h * 3600, timezone.utc).astimezone(av.LIMA)
                out.append(f"salto de contador de +{v[4]:,.0f} kWh entre las {hl:%H}:00 y las "
                           f"{(hl + timedelta(hours=1)):%H}:00")
        bajas = [(h, v) for h, v in dia if v[3] < -1]
        if bajas:
            h, v = min(bajas, key=lambda x: x[1][3])
            hl = datetime.fromtimestamp(h * 3600, timezone.utc).astimezone(av.LIMA)
            out.append(f"el contador bajó {-v[3]:,.1f} kWh (hora {hl:%H}:00)")
        return out

    def _raros_ambiental(self, k: str, d0: datetime, d1: datetime) -> list:
        out = []
        for s in self.series.get(k, []):
            ind = self.indicador.get(s, "")
            rango = RANGOS.get(ind)
            runs = self.corridas.get(s)
            if not rango or not runs:
                continue
            j = max(0, bisect_left(self._inicios_de(s), d0) - 1)
            while j < len(runs) and runs[j][0] < d1:
                ini, fin, v = runs[j]
                x = _num(v)
                if fin >= d0 and (x is None or not rango[0] <= x <= rango[1]):
                    out.append(f"{av.INDICADOR.get(ind, ind)} marcó {v} {rango[2]} (imposible)")
                    break
                j += 1
        return out

    def _snr_amb(self, k: str, fecha: date) -> tuple:
        m = self.snr.get(k) or {}
        previos = [m[fecha - timedelta(days=i)] for i in range(1, 15)
                   if fecha - timedelta(days=i) in m]
        return m.get(fecha), referencia(previos)

    def _dia_agua(self, d: av.DiaUnidad, k: str, d0: datetime, d1: datetime) -> None:
        lect = self.agua.get(k, [])
        dia = [x for x in lect if d0 <= x[0] < d1]
        previas = [x for x in lect if d0 - timedelta(days=7) <= x[0] < d0]
        snr = [x[3] for x in dia if x[3] is not None]
        d.snr = sum(snr) / len(snr) if snr else None
        por_dia: dict = {}
        for x in lect:
            if d0 - timedelta(days=14) <= x[0] < d0 and x[3] is not None:
                por_dia.setdefault(x[0].astimezone(av.LIMA).date(), []).append(x[3])
        d.snr_ref = referencia([sum(v) / len(v) for v in por_dia.values() if len(v) >= 10])
        estados = [x[2] for x in dia if x[2]]
        if estados and av.norm(estados[-1]) != "normal":
            d.estado_bateria = estados[-1]
        pasos_ref = [b[1] - a[1] for a, b in zip(previas, previas[1:])]
        tope = max(pasos_ref) if len(pasos_ref) >= 20 else None
        anterior = previas[-1] if previas else None
        bajo = salto = None
        for x in dia:
            if anterior is not None:
                p = x[1] - anterior[1]
                if p < -1 and (bajo is None or p < bajo[1]):
                    bajo = (x[0], p)
                if tope is not None and p > max(100.0, 3 * tope) and (salto is None or p > salto[1]):
                    salto = (x[0], p)
            anterior = x
        if salto:
            d.raros.append(f"salto de contador de +{salto[1]:,.0f} L a las {av.hhmm(salto[0])}")
        if bajo:
            d.raros.append(f"el contador bajó {-bajo[1]:,.0f} L a las {av.hhmm(bajo[0])}")
