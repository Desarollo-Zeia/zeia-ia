"""Motor de avisos automáticos por Telegram (fase 2): reglas → incidentes → mensajes.

Es PURO: no consulta la base ni manda nada. Recibe lo observado de cada
unidad en el instante t (lo arma src/avisos_datos.py, igual en vivo que en el
backtest) y devuelve los eventos y mensajes que tocaría mandar. El estado de
los incidentes vive en `Estado` (en vivo se guarda en SQLite: avisos_vivo.py).

Unidad = lo que se vigila: punto de energía, sensor de una sala (ambiental)
o medidor de agua.

Niveles (propuesta acordada el 19-oct-2026, ajustada con el backtest del 21-oct):
- 🔴 inmediato 24/7 con sonido:
  · plataforma sin datos: ≥80 % de un módulo sin lecturas > 30 min, en ≥2 sedes
  · gateway / sede caída: la MITAD o más de las unidades del mismo gateway (o
    de la sede si no se conoce su gateway) sin datos > 20 min, con al menos 2
    cortadas con ≤10 min de diferencia → UN aviso con todas. Si en el gateway
    solo cayó un módulo y el otro sigue reportando, lo dice (no es el gateway).
- 🟠 inmediato en horario laboral; fuera de horario espera y, si se resolvió
  antes, solo sale en el resumen:
  · sin datos ≥60 min (o 3 envíos perdidos si la unidad reporta lento); los
    que cruzan el umbral con ≤10 min de diferencia salen en un solo mensaje
  · batería < 10 %
  · transmite pero no mide: CO₂ fijo ≥3 h, todos sus indicadores fijos ≥3 h,
    otro indicador fijo ≥24 h, contador sin avanzar ≥24 h CON lecturas (y 1.5×
    lo normal en su semana), medidor de agua con tubería vacía / medición
    incorrecta ≥6 h
  · escalado: 3 días seguidos con problemas en el resumen
- 🟡 resumen diario (08:00, sin sonido) del día anterior: < 90 % de lecturas,
  un corte ≥ 60 min (lo de la noche no se avisa al momento: sale aquí),
  bloque de 6 h < 70 % (enlace degradado), SNR −10 dB vs lo normal en sus 14
  días, batería < 20 %, sin primera lectura, datos raros (saltos de contador,
  valores imposibles).
- ⚙️ el propio monitor no puede leer una base (≠ equipos caídos).

Anti-ruido: aviso de apertura y de cierre con duración (el cierre va sin
sonido y solo si se avisó la apertura); agrupado por sede y gateway;
histéresis (cierra tras 30 min estables; si vuelve a caer antes de 2 h no
avisa salvo que el corte dure 2 h); un corte de gateway o de plataforma tapa
los avisos de sus unidades; lo ya avisado (batería baja, 3 días con
problemas) solo vuelve a sonar si el corte dura 6 h; modo mantenimiento.
"""
from __future__ import annotations

import json
import math
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

LIMA = timezone(timedelta(hours=-5))

ROJO, NARANJA, AMARILLO, MONITOR = "rojo", "naranja", "amarillo", "monitor"
ICONO = {ROJO: "🔴", NARANJA: "🟠", AMARILLO: "🟡", MONITOR: "⚙️"}
ICONO_MODULO = {"energia": "⚡", "ambiental": "🌡", "agua": "💧"}
NOMBRE_MODULO = {"energia": "energía", "ambiental": "ambiental", "agua": "agua"}
BASE_DE = {"energia": "energia", "agua": "energia", "ambiental": "ambiental"}
NOMBRE_BASE = {"energia": "la base de energía (energía y agua)",
               "ambiental": "la base ambiental"}
PALABRA = {"energia": ("equipo", "equipos"), "ambiental": ("sala", "salas"),
           "agua": ("medidor", "medidores")}
INDICADOR = {"CO2": "el CO₂", "TEMPERATURE": "la temperatura", "HUMIDITY": "la humedad"}
TODO = {"todo", "todos", "todas", "all"}
ORDEN_TIPO = {"monitor": 0, "plataforma": 1, "grupo": 2, "sin_datos": 3,
              "no_mide": 4, "bateria": 5}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
DIAS_CORTO = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _hm(texto: str, defecto: int) -> int:
    """'08:00' → 480 (minutos del día); si no se entiende, `defecto`."""
    try:
        h, m = str(texto).strip().split(":")
        v = int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return defecto
    return v if 0 <= v <= 1440 else defecto


@dataclass(frozen=True)
class Reglas:
    """Umbrales (minutos salvo que el nombre diga otra cosa)."""
    sin_datos_min: int = 60          # 🟠 sin lecturas
    envios_perdidos: int = 3         # … o 3 envíos perdidos (unidades lentas)
    grupo_min: int = 20              # 🔴 corte simultáneo: sin datos > 20 min
    grupo_ventana_min: int = 10      # … al menos 2 cortados con ≤10 min de diferencia
    grupo_min_unidades: int = 2
    grupo_frac: float = 0.5          # … y la mitad o más del gateway / sede sin datos
    grupo_reciente_h: float = 24     # solo cuenta lo que se cortó en las últimas 24 h
    plataforma_min: int = 30         # 🔴 plataforma sin datos
    plataforma_frac: float = 0.8
    plataforma_min_unidades: int = 3
    estable_min: int = 30            # cierra tras 30 min estable
    reabre_min: int = 120            # recae antes de 2 h → avisa solo si dura 2 h
    conocido_min: int = 360          # ya avisado (batería, 3 días mal) → solo si dura 6 h
    bateria_critica: float = 10      # 🟠 batería (%)
    bateria_baja: float = 20         # 🟡 batería (%); el 🟠 cierra al volver a ≥20 %
    co2_fijo_h: float = 3
    todos_fijos_h: float = 3
    indicador_fijo_h: float = 24
    contador_fijo_h: float = 24
    contador_factor: float = 1.5     # … y 1.5× su mayor quietud de la semana
    agua_estado_h: float = 6
    cobertura_min: float = 90        # 🟡 día anterior < 90 %
    degradado_pct: float = 70        # 🟡 bloque de 6 h < 70 %
    snr_caida_db: float = 10
    racha_dias: int = 3              # 🟠 escalado tras 3 días en el resumen
    horario_ini: int = 8 * 60        # horario laboral (hora Lima) para 🟠
    horario_fin: int = 20 * 60
    resumen_min: int = 8 * 60        # hora del resumen diario
    monitor_fallos: int = 3          # ciclos seguidos sin poder leer → ⚙️
    max_lineas: int = 15             # filas por sección del resumen

    @classmethod
    def desde_config(cls, horario: str = "08:00-20:00", resumen: str = "08:00") -> "Reglas":
        ini, _, fin = str(horario or "").partition("-")
        return cls(horario_ini=_hm(ini, 8 * 60), horario_fin=_hm(fin, 20 * 60),
                   resumen_min=_hm(resumen, 8 * 60))


REGLAS = Reglas()


# ---------- Umbrales por cadencia ------------------------------------------------

def _cad(c) -> int:
    return max(1, int(c or 1))


def minimo_corte(c: int) -> int:
    """Minutos sin cubrir desde los que un hueco es corte (igual que foto_salas)."""
    return max(5, _cad(c) + 2)


def hueco_corte(c: int) -> int:
    """Minutos entre dos lecturas seguidas desde los que hay corte.

    Energía (1/min): 6 min; ambiental (5 min): 12 min = 2 lecturas perdidas
    (una suelta no cuenta); agua cada 30 min: 62 min.
    """
    return _cad(c) + minimo_corte(c)


def umbral_sin_datos(c: int, r: Reglas = REGLAS) -> int:
    c = _cad(c)
    return max(r.sin_datos_min, r.envios_perdidos * c + c // 2)


def umbral_grupo(c: int, r: Reglas = REGLAS) -> int:
    c = _cad(c)
    return max(r.grupo_min, r.envios_perdidos * c + c // 2)


def hueco_estable(c: int, r: Reglas = REGLAS) -> int:
    """Hueco que impide dar por "de vuelta" a una unidad: medio umbral de sin
    datos (30 min; agua cada 30 min: 62). Más largo que un corte: una unidad
    que volvió pero pierde paquetes (enlace degradado, como Zona Verde con la
    batería baja) se da por vuelta; lo intermitente sale en el resumen.
    """
    return max(hueco_corte(c), umbral_sin_datos(c, r) // 2)


def ventana_estable(c: int, r: Reglas = REGLAS) -> int:
    """Minutos sin huecos ≥ hueco_estable para dar por vuelta a una unidad (60)."""
    return max(r.estable_min, 2 * hueco_estable(c, r))


# ---------- Textos ------------------------------------------------------------------

def lima(t: datetime) -> datetime:
    return t.astimezone(LIMA)


def hhmm(t: datetime) -> str:
    return f"{lima(t):%H:%M}"


def cuando(t: Optional[datetime], ref: datetime) -> str:
    """'las 11:40' (mismo día que ref, hora Lima), 'ayer 11:40' o 'el 17-oct 11:40'."""
    if t is None:
        return "—"
    tl, rl = lima(t), lima(ref)
    if tl.date() == rl.date():
        return f"las {tl:%H:%M}"
    if tl.date() == rl.date() - timedelta(days=1):
        return f"ayer {tl:%H:%M}"
    return f"el {tl.day:02d}-{MESES[tl.month - 1]} {tl:%H:%M}"


def duracion(minutos) -> str:
    m = max(0, int(round(float(minutos))))
    if m < 60:
        return f"{m} min"
    if m < 1440:
        h, r = divmod(m, 60)
        return f"{h} h {r} min" if r else f"{h} h"
    d, h = m // 1440, (m % 1440) // 60
    return (f"{d} día" if d == 1 else f"{d} días") + (f" {h} h" if h else "")


def _min(td: timedelta) -> float:
    return td.total_seconds() / 60


def fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"


def pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v < 100 and round(v, 1) >= 100:
        v = 99.9
    return f"{v:.1f}".rstrip("0").rstrip(".") + "%"


def en_horario(t: datetime, r: Reglas = REGLAS) -> bool:
    tl = lima(t)
    m = tl.hour * 60 + tl.minute
    return r.horario_ini <= m < r.horario_fin


def norm(texto) -> str:
    """Minúsculas, sin tildes y con espacios simples (para buscar por nombre)."""
    s = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _dt(v) -> Optional[datetime]:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(v)


# ---------- Unidades y observaciones ---------------------------------------------------

@dataclass
class Unidad:
    clave: str                   # "energia:3", "ambiental:147", "agua:2"
    modulo: str                  # energia | ambiental | agua
    nombre: str                  # punto (energía) / sala (ambiental) / tubería (agua)
    grupo: str = ""              # tablero (energía) / medidor (agua) / "" (ambiental)
    empresa: str = ""
    sede: str = ""
    gateway: Optional[str] = None
    cadencia: int = 1            # minutos entre lecturas (moda de su semana)

    @property
    def tienda(self) -> str:
        return f"{self.empresa} {self.sede}".strip()

    @property
    def alcance(self) -> str:
        """Dónde se buscan cortes simultáneos: su gateway (si se conoce) o su sede."""
        return f"{self.gateway} · {self.tienda}" if self.gateway else self.tienda

    @property
    def donde(self) -> str:
        return f"{self.tienda} · {self.grupo}" if self.grupo else self.tienda

    def textos(self) -> list:
        return [self.modulo, NOMBRE_MODULO.get(self.modulo, ""), self.empresa, self.sede,
                self.tienda, self.gateway or "", self.nombre, self.grupo]


class Obs:
    """Lo que se sabe de una unidad en el instante t.

    avisos_datos.Historia la arma con búsquedas en su presencia minuto a
    minuto; con `lecturas` (lista de datetimes) sirve sola, p. ej. en pruebas.
    """

    def __init__(self, unidad: Unidad, ultima: Optional[datetime] = None,
                 bateria: Optional[float] = None, plano_h: Optional[float] = None,
                 plano_ref_h: Optional[float] = None, fijos: Optional[dict] = None,
                 estado_agua: Optional[tuple] = None, lecturas: Optional[list] = None,
                 contador_cero: bool = False):
        self.unidad = unidad
        self.ultima = ultima                # última lectura antes de t
        self.bateria = bateria              # % (ambiental)
        self.plano_h = plano_h              # horas CON lecturas sin que el contador avance
        self.plano_ref_h = plano_ref_h      # mayor quietud de su semana previa
        self.contador_cero = contador_cero  # en esa quietud el contador llega en 0
        self.fijos = fijos or {}            # indicador → horas con el mismo valor
        self.estado_agua = estado_agua      # (texto del estado anormal, horas)
        self._lecturas = sorted(lecturas) if lecturas is not None else None

    def primera_desde(self, t0: datetime, t: datetime) -> Optional[datetime]:
        """Primera lectura posterior a t0 (y anterior a t)."""
        if self._lecturas is None:
            return self.ultima if self.ultima and t0 < self.ultima < t else None
        return next((x for x in self._lecturas if t0 < x < t), None)

    def estable(self, t: datetime, minutos: int, hueco: Optional[int] = None) -> bool:
        """Sin huecos ≥ `hueco` (por defecto, un corte) en los últimos `minutos`."""
        g = timedelta(minutes=hueco or hueco_corte(self.unidad.cadencia))
        if self._lecturas is None:
            return self.ultima is not None and t - self.ultima < g
        ini = t - timedelta(minutes=minutos)
        marcas = [ini] + [x for x in self._lecturas if ini <= x < t] + [t]
        return len(marcas) > 2 and all(b - a < g for a, b in zip(marcas, marcas[1:]))

    def regreso(self, t: datetime, desde: datetime, hueco: Optional[int] = None) -> Optional[datetime]:
        """Desde cuándo llegan datos sin huecos ≥ `hueco`: primera lectura tras el último."""
        if self._lecturas is None:
            return self.primera_desde(desde, t)
        g = timedelta(minutes=hueco or hueco_corte(self.unidad.cadencia))
        prev, res = desde, None
        for x in self._lecturas:
            if x <= desde:
                continue
            if x >= t:
                break
            if x - prev >= g:
                res = x
            prev = x
        return res


# ---------- Incidentes, estado y mensajes -------------------------------------------------

@dataclass
class Incidente:
    clave: str                   # "sin_datos|energia:3", "grupo|<alcance>", "plataforma|energia"…
    tipo: str                    # monitor | plataforma | grupo | sin_datos | no_mide | bateria
    nivel: str
    abierto: datetime            # instante en que se abrió
    desde: datetime              # inicio real (p. ej. la última lectura antes del corte)
    nombre: str = ""
    tienda: str = ""
    grupo: str = ""
    modulo: str = ""
    unidades: list = field(default_factory=list)
    detalle: dict = field(default_factory=dict)
    avisado: bool = False        # se mandó el aviso de apertura
    notificar_desde: Optional[datetime] = None   # histéresis: no avisar antes de esto
    recuperando: Optional[datetime] = None       # condición falsa desde
    cortes: int = 1
    cerrado: Optional[datetime] = None

    _FECHAS = ("abierto", "desde", "notificar_desde", "recuperando", "cerrado")

    @property
    def donde(self) -> str:
        return f"{self.tienda} · {self.grupo}" if self.grupo else self.tienda

    def a_dict(self) -> dict:
        d = asdict(self)
        for k in self._FECHAS:
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def de_dict(cls, d: dict) -> "Incidente":
        d = dict(d)
        for k in cls._FECHAS:
            d[k] = _dt(d.get(k))
        return cls(**d)


@dataclass
class Estado:
    abiertos: dict = field(default_factory=dict)     # clave → Incidente
    cerrados: dict = field(default_factory=dict)     # clave → {"t": iso, "cortes": n}
    rachas: dict = field(default_factory=dict)       # unidad → {"dias", "hist", "ultimo", "escalado"}
    fallos: dict = field(default_factory=dict)       # base → {"n", "desde", "error"}
    ultimo_resumen: Optional[str] = None             # fecha Lima del último resumen
    sin_primera: list = field(default_factory=list)  # claves listadas en el último resumen
    ultimo_ciclo: Optional[str] = None

    def a_json(self) -> str:
        return json.dumps({
            "abiertos": {k: i.a_dict() for k, i in self.abiertos.items()},
            "cerrados": self.cerrados, "rachas": self.rachas, "fallos": self.fallos,
            "ultimo_resumen": self.ultimo_resumen, "sin_primera": self.sin_primera,
            "ultimo_ciclo": self.ultimo_ciclo}, ensure_ascii=False)

    @classmethod
    def de_json(cls, texto: Optional[str]) -> "Estado":
        if not texto:
            return cls()
        d = json.loads(texto)
        return cls(abiertos={k: Incidente.de_dict(v) for k, v in (d.get("abiertos") or {}).items()},
                   cerrados=d.get("cerrados") or {}, rachas=d.get("rachas") or {},
                   fallos=d.get("fallos") or {}, ultimo_resumen=d.get("ultimo_resumen"),
                   sin_primera=d.get("sin_primera") or [], ultimo_ciclo=d.get("ultimo_ciclo"))


@dataclass
class Evento:
    tipo: str                    # abre | cierra
    inc: Incidente
    t: datetime
    avisar: bool = True          # cierre: solo se avisa si se avisó la apertura


@dataclass
class Mensaje:
    texto: str
    nivel: str
    silencioso: bool
    t: datetime
    tipo: str = "aviso"          # aviso | cierre | resumen | escalado


@dataclass
class Mantenimiento:
    alcance: str                 # "todo", empresa, sede, gateway, sala, equipo o módulo
    hasta: datetime
    desde: Optional[datetime] = None
    motivo: str = ""
    por: str = ""
    id: Optional[int] = None

    def vigente(self, t: datetime) -> bool:
        return (self.desde is None or self.desde <= t) and t < self.hasta


def cubre_unidad(m: Mantenimiento, u: Unidad) -> bool:
    a = norm(m.alcance)
    if a in TODO:
        return True
    return bool(a) and any(a in norm(x) for x in u.textos() if x)


def unidades_en(alcance: str, unidades: Iterable) -> list:
    """Unidades que cubre un texto de mantenimiento."""
    m = Mantenimiento(alcance, datetime.max.replace(tzinfo=timezone.utc))
    return [u for u in unidades if cubre_unidad(m, u)]


def en_mantenimiento(inc: Incidente, mants: Iterable, unidades: dict, t: datetime) -> bool:
    for m in mants:
        if not m.vigente(t):
            continue
        a = norm(m.alcance)
        if a in TODO:
            return True
        textos = [inc.nombre, inc.tienda, inc.grupo, inc.modulo,
                  NOMBRE_MODULO.get(inc.modulo, ""), str(inc.detalle.get("alcance") or ""),
                  str(inc.detalle.get("base") or "")]
        for k in inc.unidades:
            if k in unidades:
                textos += unidades[k].textos()
        if a and any(a in norm(x) for x in textos if x):
            return True
    return False


# ---------- Evaluación ------------------------------------------------------------------

@dataclass
class Cond:
    tipo: str
    nivel: str
    desde: datetime
    nombre: str
    tienda: str = ""
    grupo: str = ""
    modulo: str = ""
    unidades: list = field(default_factory=list)
    detalle: dict = field(default_factory=dict)


def _cond_de(o: Obs, tipo: str, nivel: str, desde: datetime, **detalle) -> Cond:
    u = o.unidad
    return Cond(tipo, nivel, desde, u.nombre, u.tienda, u.grupo, u.modulo, [u.clave], dict(detalle))


def _no_mide(o: Obs, r: Reglas) -> Optional[tuple]:
    """(motivo legible, horas) si la unidad transmite pero no mide."""
    if o.plano_h is not None and o.plano_ref_h is not None:
        lim = max(r.contador_fijo_h, r.contador_factor * o.plano_ref_h)
        if o.plano_h >= lim:
            dur = duracion(o.plano_h * 60)
            normal = ("normalmente avanza cada hora" if o.plano_ref_h < 1 else
                      f"en su semana estuvo hasta {duracion(o.plano_ref_h * 60)} sin avanzar")
            if o.contador_cero:
                return f"envía el contador en 0 hace {dur} ({normal})", o.plano_h
            return (f"el contador no avanza hace {dur} aunque llegan lecturas ({normal}); "
                    "puede ser el equipo apagado", o.plano_h)
    if o.fijos:
        co2 = o.fijos.get("CO2")
        if co2 is not None and co2 >= r.co2_fijo_h:
            return f"el CO₂ marca el mismo valor hace {duracion(co2 * 60)}", co2
        if len(o.fijos) >= 2 and min(o.fijos.values()) >= r.todos_fijos_h:
            h = min(o.fijos.values())
            return f"todos sus indicadores marcan el mismo valor hace {duracion(h * 60)}", h
        for ind, h in sorted(o.fijos.items()):
            if h >= r.indicador_fijo_h:
                return (f"{INDICADOR.get(ind, ind)} marca el mismo valor hace "
                        f"{duracion(h * 60)}"), h
    if o.estado_agua and o.estado_agua[1] >= r.agua_estado_h:
        return f"reporta {o.estado_agua[0]} hace {duracion(o.estado_agua[1] * 60)}", o.estado_agua[1]
    return None


def _cond_unidades(vivas: list, t: datetime, r: Reglas, conds: dict) -> None:
    for o in vivas:
        u = o.unidad
        fresca = (o.ultima is not None
                  and _min(t - o.ultima) < umbral_sin_datos(u.cadencia, r))
        if o.ultima is not None and not fresca:
            conds[f"sin_datos|{u.clave}"] = _cond_de(o, "sin_datos", NARANJA, o.ultima)
        if o.bateria is not None and o.bateria < r.bateria_critica:
            conds[f"bateria|{u.clave}"] = _cond_de(o, "bateria", NARANJA, t,
                                                   bateria=float(o.bateria))
        if fresca:
            motivo = _no_mide(o, r)
            if motivo:
                conds[f"no_mide|{u.clave}"] = _cond_de(
                    o, "no_mide", NARANJA, t - timedelta(hours=motivo[1]),
                    motivo=motivo[0], horas=round(float(motivo[1]), 2))


def _cond_plataforma(vivas: list, t: datetime, r: Reglas, conds: dict) -> set:
    """Abre la condición de plataforma y devuelve los módulos SOSPECHOSOS.

    Sospechoso = ya tiene ≥80 % caído hace ≥ grupo_min (20) pero aún no
    llega a plataforma_min (30): los cortes de gateway de ese módulo esperan,
    para mandar UN aviso de plataforma y no uno por gateway.
    """
    por: dict = {}
    for o in vivas:
        por.setdefault(o.unidad.modulo, []).append(o)
    sospecha = set()
    for mod, os in por.items():
        if len(os) < r.plataforma_min_unidades:
            continue
        for minutos in (r.plataforma_min, r.grupo_min):
            lim = timedelta(minutes=minutos)
            abajo = [o for o in os if o.ultima is not None and t - o.ultima >= lim]
            tiendas = sorted({o.unidad.tienda for o in abajo})
            if len(abajo) < r.plataforma_frac * len(os) or len(tiendas) < 2:
                continue
            if minutos == r.grupo_min:
                sospecha.add(mod)
                continue
            conds[f"plataforma|{mod}"] = Cond(
                "plataforma", ROJO, max(o.ultima for o in abajo),
                f"Plataforma de {NOMBRE_MODULO.get(mod, mod)}", modulo=mod,
                unidades=[o.unidad.clave for o in abajo],
                detalle={"abajo": len(abajo), "total": len(os), "tiendas": tiendas})
            break
    return sospecha


def _conocido(estado: Estado, clave_unidad: str, t: datetime) -> Optional[str]:
    """¿Ya se avisó algo de esta unidad que explica sus cortes? (batería, racha de días)."""
    for i in estado.abiertos.values():
        if i.avisado and i.tipo == "bateria" and clave_unidad in i.unidades:
            return "ya tenía la batería baja"
    racha = estado.rachas.get(clave_unidad) or {}
    if racha.get("escalado") and racha.get("ultimo"):
        dias = (lima(t).date() - date.fromisoformat(racha["ultimo"])).days
        if dias <= 2:
            return f"lleva {racha.get('dias')} días con problemas"
    return None


def _abrir(estado: Estado, clave: str, c: Cond, t: datetime, r: Reglas) -> Incidente:
    inc = estado.abiertos.get(clave)
    if inc is not None:
        inc.recuperando = None
        inc.detalle.update(c.detalle)
        return inc
    inc = Incidente(clave, c.tipo, c.nivel, abierto=t, desde=c.desde, nombre=c.nombre,
                    tienda=c.tienda, grupo=c.grupo, modulo=c.modulo,
                    unidades=list(c.unidades), detalle=dict(c.detalle))
    prev = estado.cerrados.get(clave)
    if prev:
        cerrado = _dt(prev["t"])
        if c.desde - cerrado < timedelta(minutes=r.reabre_min):
            # Recayó poco después de cerrarse: avisar solo si el corte dura 2 h.
            inc.cortes = int(prev.get("cortes") or 1) + 1
            inc.notificar_desde = c.desde + timedelta(minutes=r.reabre_min)
    if c.tipo == "sin_datos" and c.unidades:
        motivo = _conocido(estado, c.unidades[0], t)
        if motivo:              # problema ya avisado: que no suene por cada corte
            inc.detalle["conocido"] = motivo
            inc.notificar_desde = max(inc.notificar_desde or c.desde,
                                      c.desde + timedelta(minutes=r.conocido_min))
    estado.abiertos[clave] = inc
    return inc


def _cerrar(estado: Estado, inc: Incidente, t: datetime, eventos: list,
            avisar: bool = True, **extra) -> None:
    estado.abiertos.pop(inc.clave, None)
    inc.cerrado = t
    inc.detalle.update(extra)
    estado.cerrados[inc.clave] = {"t": t.isoformat(), "cortes": inc.cortes}
    eventos.append(Evento("cierra", inc, t, avisar=avisar and inc.avisado))


def _temporizador(inc: Incidente, t: datetime, r: Reglas) -> bool:
    """La condición ya no se cumple: ¿lleva estable_min así?"""
    if inc.recuperando is None:
        inc.recuperando = t
    return t - inc.recuperando >= timedelta(minutes=r.estable_min)


def _revisar_cierre(estado: Estado, inc: Incidente, por_clave: dict, t: datetime,
                    r: Reglas, eventos: list) -> None:
    if inc.tipo == "plataforma":
        if _temporizador(inc, t, r):
            _cerrar(estado, inc, t, eventos, regreso=inc.recuperando.isoformat())
        return
    o = por_clave.get(inc.unidades[0]) if inc.unidades else None
    if o is None:                       # ya no se vigila (o salió del inventario)
        _cerrar(estado, inc, t, eventos, avisar=False)
        return
    c = o.unidad.cadencia
    if inc.tipo == "sin_datos":
        if o.ultima is None or o.ultima <= inc.desde:
            return
        h = hueco_estable(c, r)
        if o.estable(t, ventana_estable(c, r), h):
            vuelta = o.regreso(t, inc.desde, h) or o.ultima
            # Si es parte de un corte de gateway ya avisado, el cierre del grupo lo cuenta.
            en_grupo = any(i.tipo == "grupo" and i.avisado and o.unidad.clave in i.unidades
                           for i in estado.abiertos.values())
            _cerrar(estado, inc, t, eventos, avisar=not en_grupo, regreso=vuelta.isoformat())
    elif inc.tipo == "bateria":
        if o.bateria is not None and o.bateria >= r.bateria_baja:
            _cerrar(estado, inc, t, eventos, bateria_cierre=float(o.bateria))
    elif inc.tipo == "no_mide":
        if o.ultima is None or _min(t - o.ultima) >= umbral_sin_datos(c, r):
            return                      # sin datos frescos no se puede saber
        if _temporizador(inc, t, r):
            _cerrar(estado, inc, t, eventos)


def _tipo_txt(u: Unidad) -> str:
    if u.modulo == "ambiental":
        return "sala"
    base = NOMBRE_MODULO.get(u.modulo, u.modulo)
    return f"{base} · {u.grupo}" if u.grupo else base


def _miembro(o: Obs) -> dict:
    return {"nombre": o.unidad.nombre, "tipo": _tipo_txt(o.unidad), "modulo": o.unidad.modulo,
            "desde": o.ultima.isoformat(), "volvio": None}


def _grupos(estado: Estado, vivas: list, por_clave: dict, caidos: set, t: datetime,
            r: Reglas, eventos: list) -> None:
    """Gateway o sede caída: abrir, sumar lo que se va cayendo y cerrar.

    Solo cuenta lo cortado en las últimas `grupo_reciente_h` horas: un sensor
    muerto hace días no es parte de un corte nuevo (ni del total del gateway).
    Abre si se cortó la MITAD o más de lo que reportaba en el gateway (o en la
    sede), con al menos 2 cortados con ≤ grupo_ventana_min de diferencia, o si
    3 o más se cortaron a la vez. Mientras sigue abierto, lo que se corta en
    el mismo gateway se suma (las caídas escalonadas, como la del 06-oct, son
    el mismo evento).
    """
    v = timedelta(minutes=r.grupo_ventana_min)
    reciente = timedelta(hours=r.grupo_reciente_h)
    abajo: dict = {}
    base: Counter = Counter()           # lo que reportaba antes del corte
    por_mod: dict = {}
    for o in vivas:
        a = o.unidad.alcance
        cortada = (o.ultima is not None
                   and _min(t - o.ultima) >= umbral_grupo(o.unidad.cadencia, r))
        if cortada and t - o.ultima > reciente:
            continue                    # caída de antes: se sigue por su cuenta
        base[a] += 1
        por_mod.setdefault(a, Counter())[o.unidad.modulo] += 1
        if cortada:
            abajo.setdefault(a, []).append(o)

    abiertos = {i.detalle.get("alcance"): i for i in list(estado.abiertos.values())
                if i.tipo == "grupo"}
    for alcance, inc in abiertos.items():
        miembros = inc.detalle["miembros"]
        if any(m.get("modulo") in caidos for m in miembros.values()):
            continue                    # una base no se pudo leer: no se decide nada
        desdes = [_dt(m["desde"]) for m in miembros.values()]
        lo, hi = min(desdes) - v, max(desdes) + reciente
        for o in sorted(abajo.get(alcance, []), key=lambda o: o.ultima):
            if o.unidad.clave not in miembros and lo <= o.ultima <= hi:
                miembros[o.unidad.clave] = _miembro(o)
                hi = max(hi, o.ultima + reciente)
        siguen = []
        for k, m in miembros.items():
            o, d = por_clave.get(k), _dt(m["desde"])
            if o is None:
                continue                # ya no se vigila
            h = hueco_estable(o.unidad.cadencia, r)
            if (o.ultima is not None and o.ultima > d
                    and o.estable(t, ventana_estable(o.unidad.cadencia, r), h)):
                if not m.get("volvio"):
                    m["volvio"] = (o.regreso(t, d, h) or o.ultima).isoformat()
            else:
                m["volvio"] = None
                siguen.append(k)
        inc.unidades = list(miembros)
        inc.detalle["total"] = max(int(inc.detalle.get("total") or 0), base[alcance])
        inc.detalle["abajo_ahora"] = sum(
            1 for k in miembros if k in por_clave and por_clave[k].ultima is not None
            and _min(t - por_clave[k].ultima) >= umbral_grupo(por_clave[k].unidad.cadencia, r))
        if len(siguen) > 1:
            inc.recuperando = None
            continue
        # Si queda uno solo, se espera estable_min: suelen volver con minutos de
        # diferencia y así sale UN cierre en vez de "sigue sin datos" + otro ✅.
        if siguen and not _temporizador(inc, t, r):
            continue
        inc.detalle["siguen"] = siguen
        _cerrar(estado, inc, t, eventos)
        for k in siguen:               # el que sigue caído hereda el aviso del grupo
            o = por_clave[k]
            si = _abrir(estado, f"sin_datos|{k}",
                        _cond_de(o, "sin_datos", NARANJA, _dt(miembros[k]["desde"])), t, r)
            si.avisado = si.avisado or inc.avisado

    for alcance, os in abajo.items():
        previo = abiertos.get(alcance)
        if previo is not None and previo.clave in estado.abiertos:
            continue
        os.sort(key=lambda o: o.ultima)
        juntos = _racimo([o.ultima for o in os], v)
        mitad = math.ceil(r.grupo_frac * base[alcance] - 1e-9)
        # La mitad o más de lo que reportaba (con 2 cortados a la vez), o 3 o más
        # cortados a la vez. Dos sueltos que pierden paquetes no son un gateway.
        if not ((len(os) >= max(r.grupo_min_unidades, mitad) and juntos >= 2)
                or juntos >= r.grupo_min_unidades + 1):
            continue
        u0 = os[0].unidad
        mods = {o.unidad.modulo for o in os}
        c = Cond("grupo", ROJO, os[0].ultima, alcance, u0.tienda, "",
                 mods.pop() if len(mods) == 1 else "", [o.unidad.clave for o in os],
                 {"alcance": alcance, "gateway": u0.gateway, "total": base[alcance],
                  "modulos": dict(por_mod.get(alcance, {})), "abajo_ahora": len(os),
                  "miembros": {o.unidad.clave: _miembro(o) for o in os}})
        _abrir(estado, f"grupo|{alcance}", c, t, r)


def _caido(d: dict) -> tuple:
    """(¿gateway o sede caída?, módulos que siguen reportando enteros, cuántos siguen).

    Caída = la mitad o más de lo que reportaba sigue sin datos y ningún módulo
    del mismo gateway sigue reportando entero (si todas sus salas reportan,
    el gateway funciona: lo caído es otra cosa).
    """
    todos = list(d.get("miembros", {}).values())
    abajo = [m for m in todos if not m.get("volvio")]
    cortados = Counter(m.get("modulo") for m in abajo)
    modulos = d.get("modulos") or {}
    otro_ok = [mod for mod, k in modulos.items() if k and not cortados.get(mod)]
    total = max(int(d.get("total") or 0), len(todos))
    return (len(abajo) * 2 >= total and not otro_ok), otro_ok, total - len(abajo)


def _racimo(tiempos: list, ventana: timedelta) -> int:
    """Cuántos, como máximo, caen dentro de una misma ventana (tiempos ordenados)."""
    mejor, i = 0, 0
    for j, x in enumerate(tiempos):
        while x - tiempos[i] > ventana:
            i += 1
        mejor = max(mejor, j - i + 1)
    return mejor


def _monitor(estado: Estado, t: datetime, r: Reglas, errores: dict, eventos: list) -> None:
    """El monitor no pudo leer una base: ⚙️ tras `monitor_fallos` ciclos seguidos."""
    for base in sorted(set(BASE_DE.values())):
        clave = f"monitor|{base}"
        if base in errores:
            f = estado.fallos.setdefault(base, {"n": 0, "desde": t.isoformat()})
            f["n"] = int(f.get("n") or 0) + 1
            f["error"] = str(errores[base])[:200]
            if f["n"] >= r.monitor_fallos and clave not in estado.abiertos:
                estado.abiertos[clave] = Incidente(
                    clave, "monitor", MONITOR, abierto=t, desde=_dt(f["desde"]),
                    nombre=NOMBRE_BASE.get(base, base),
                    detalle={"base": base, "error": f["error"],
                             "modulos": [m for m, b in BASE_DE.items() if b == base]})
        else:
            estado.fallos.pop(base, None)
            inc = estado.abiertos.get(clave)
            if inc is not None:
                _cerrar(estado, inc, t, eventos)


def _notificable(nivel: str, t: datetime, r: Reglas) -> bool:
    if nivel in (ROJO, MONITOR):
        return True
    return nivel == NARANJA and en_horario(t, r)


def cubierto(inc: Incidente, abiertos: dict) -> bool:
    """¿Lo tapa una plataforma caída o un corte de gateway/sede abierto?"""
    plataformas = {i.modulo for i in abiertos.values() if i.tipo == "plataforma"}
    if inc.tipo == "sin_datos":
        if inc.modulo in plataformas:
            return True
        return any(i.tipo == "grupo" and inc.unidades and inc.unidades[0] in i.unidades
                   for i in abiertos.values())
    if inc.tipo == "grupo":
        mods = {m.get("modulo") for m in inc.detalle.get("miembros", {}).values()}
        return bool(mods) and mods <= plataformas
    return False


def _adelantar(estado: Estado, inc: Incidente, vivas: list, t: datetime, r: Reglas,
               mants: list, unidades: dict, eventos: list) -> None:
    """Junto con `inc` avisa los de su sede cortados ≤10 min antes/después que
    cruzarían el umbral en los próximos minutos: UN mensaje en vez de varios."""
    v = timedelta(minutes=r.grupo_ventana_min)
    for o in vivas:
        u, k = o.unidad, o.unidad.clave
        clave = f"sin_datos|{k}"
        if (u.tienda != inc.tienda or clave in estado.abiertos or o.ultima is None
                or abs(o.ultima - inc.desde) > v):
            continue
        umbral = umbral_sin_datos(u.cadencia, r)
        if _min(t - o.ultima) < umbral - r.grupo_ventana_min or _conocido(estado, k, t):
            continue
        prev = estado.cerrados.get(clave)
        if prev and o.ultima - _dt(prev["t"]) < timedelta(minutes=r.reabre_min):
            continue
        nuevo = _abrir(estado, clave, _cond_de(o, "sin_datos", NARANJA, o.ultima), t, r)
        if cubierto(nuevo, estado.abiertos) or en_mantenimiento(nuevo, mants, unidades, t):
            continue                    # queda abierto y sigue el camino normal
        nuevo.avisado = True
        eventos.append(Evento("abre", nuevo, t))


def _escalar_grupos(estado: Estado, t: datetime, mants: list, unidades: dict,
                    eventos: list, sospecha: set, vigentes: set) -> None:
    """Un corte parcial ya avisado que crece hasta tumbar el gateway: avisarlo UNA vez."""
    for inc in estado.abiertos.values():
        if (inc.tipo != "grupo" or not inc.avisado or inc.detalle.get("escalado")
                or inc.clave not in vigentes or cubierto(inc, estado.abiertos)
                or sospecha & _modulos_grupo(inc) or en_mantenimiento(inc, mants, unidades, t)):
            continue
        if _caido(inc.detalle)[0] and not inc.detalle.get("caido_al_avisar"):
            inc.detalle["escalado"] = True
            eventos.append(Evento("abre", inc, t))


def _modulos_grupo(inc: Incidente) -> set:
    return {m.get("modulo") for m in inc.detalle.get("miembros", {}).values()}


def _avisar(estado: Estado, t: datetime, r: Reglas, mants: list, unidades: dict,
            eventos: list, sospecha: set = frozenset(), vivas: Iterable = (),
            vigentes: Optional[set] = None) -> None:
    """Avisa lo abierto que corresponde. `vigentes` = claves cuya condición se
    cumple AHORA: lo que ya volvió y está esperando su cierre no se avisa tarde
    (p. ej. a las 08:00 algo que se cortó de noche y volvió a las 07:50)."""
    vig = set(estado.abiertos) if vigentes is None else vigentes
    _escalar_grupos(estado, t, mants, unidades, eventos, set(sospecha), vig)
    for inc in sorted(estado.abiertos.values(),
                      key=lambda i: (ORDEN_TIPO.get(i.tipo, 9), i.abierto, i.clave)):
        if inc.avisado or inc.clave not in vig or cubierto(inc, estado.abiertos):
            continue
        if inc.notificar_desde and t < inc.notificar_desde:
            continue
        if inc.tipo == "grupo" and sospecha & _modulos_grupo(inc):
            continue                    # quizá es la plataforma entera: esperar a confirmarlo
        if en_mantenimiento(inc, mants, unidades, t) or not _notificable(inc.nivel, t, r):
            continue
        inc.avisado = True
        if inc.tipo == "grupo":
            inc.detalle["caido_al_avisar"] = _caido(inc.detalle)[0]
        eventos.append(Evento("abre", inc, t))
        if inc.tipo == "sin_datos":
            _adelantar(estado, inc, list(vivas), t, r, mants, unidades, eventos)


def evaluar(estado: Estado, obs: list, t: datetime, reglas: Optional[Reglas] = None,
            mantenimientos: Iterable = (), errores: Optional[dict] = None) -> list:
    """Actualiza `estado` con lo observado en t y devuelve los eventos (abre/cierra).

    errores: {base: texto} de las bases que no se pudieron leer en este ciclo;
    sus unidades no se evalúan (sus incidentes quedan como estaban).
    """
    r = reglas or REGLAS
    eventos: list = []
    errores = {b: e for b, e in (errores or {}).items() if e}
    _monitor(estado, t, r, errores, eventos)
    caidos = {m for m, b in BASE_DE.items() if b in errores}
    vivas = [o for o in obs if o.unidad.modulo not in caidos]
    por_clave = {o.unidad.clave: o for o in vivas}
    unidades = {o.unidad.clave: o.unidad for o in obs}

    conds: dict = {}
    sospecha = _cond_plataforma(vivas, t, r, conds)
    _cond_unidades(vivas, t, r, conds)
    for clave, c in conds.items():
        _abrir(estado, clave, c, t, r)
    for clave, inc in list(estado.abiertos.items()):
        if inc.tipo in ("grupo", "monitor") or clave in conds or inc.modulo in caidos:
            continue
        _revisar_cierre(estado, inc, por_clave, t, r, eventos)
    _grupos(estado, vivas, por_clave, caidos, t, r, eventos)
    vigentes = set(conds) | {
        k for k, i in estado.abiertos.items()
        if i.tipo == "monitor"
        or (i.tipo == "grupo" and int(i.detalle.get("abajo_ahora") or 0) >= r.grupo_min_unidades)}
    _avisar(estado, t, r, list(mantenimientos), unidades, eventos, sospecha, vivas, vigentes)
    viejo = t - timedelta(days=1)
    estado.cerrados = {k: v for k, v in estado.cerrados.items() if _dt(v["t"]) > viejo}
    estado.ultimo_ciclo = t.isoformat()
    return eventos


# ---------- Mensajes inmediatos --------------------------------------------------------

def _por(items: list, clave) -> list:
    """[(clave, [items])] respetando el orden de aparición."""
    grupos: dict = {}
    for x in items:
        grupos.setdefault(clave(x), []).append(x)
    return list(grupos.items())


def _palabra(modulos: Iterable, n: int) -> str:
    mods = set(modulos)
    sing, plur = PALABRA.get(mods.pop(), ("equipo", "equipos")) if len(mods) == 1 \
        else ("equipo", "equipos")
    return sing if n == 1 else plur


def _quien(inc: Incidente, con_tienda: bool) -> str:
    icono = ICONO_MODULO.get(inc.modulo, "")
    lugar = inc.donde if con_tienda else inc.grupo
    return f"{icono} {inc.nombre}".strip() + (f" ({lugar})" if lugar else "")


def _linea_abre(inc: Incidente, t: datetime, con_tienda: bool) -> str:
    q = _quien(inc, con_tienda)
    if inc.tipo == "sin_datos":
        extra = f"; se cortó {inc.cortes} veces seguidas" if inc.cortes > 1 else ""
        if inc.detalle.get("conocido"):
            extra += f"; {inc.detalle['conocido']}"
        return (f"{q} sin datos desde {cuando(inc.desde, t)} "
                f"(hace {duracion(_min(t - inc.desde))}){extra}")
    if inc.tipo == "bateria":
        return f"{q}: batería {inc.detalle.get('bateria', 0):.0f} %, cambiarla pronto"
    if inc.tipo == "no_mide":
        return f"{q} transmite pero no mide: {inc.detalle.get('motivo', '')}"
    return q


def _linea_cierra(inc: Incidente, t: datetime, con_tienda: bool) -> str:
    q = _quien(inc, con_tienda)
    if inc.tipo == "sin_datos":
        vuelta = _dt(inc.detalle.get("regreso")) or t
        return (f"{q} volvió a {cuando(vuelta, t)} "
                f"({duracion(_min(vuelta - inc.desde))} sin datos)")
    if inc.tipo == "bateria":
        return f"{q}: batería {inc.detalle.get('bateria_cierre', 0):.0f} % (la cambiaron)"
    if inc.tipo == "no_mide":
        return f"{q} volvió a medir (el problema venía desde {cuando(inc.desde, t)})"
    return q


MAX_NOMBRES = 8                 # nombres por mensaje de grupo (el resto: "y N más")


def _lista(nombres: list, maximo: int = MAX_NOMBRES) -> str:
    if len(nombres) <= maximo:
        return ", ".join(nombres)
    return ", ".join(nombres[:maximo]) + f" y {len(nombres) - maximo} más"


def _bloques(ms: list) -> list:
    """Miembros por módulo y tablero: [(etiqueta, [nombres])] en orden de caída."""
    out: dict = {}
    for m in sorted(ms, key=lambda m: m["desde"]):
        etiqueta = "Salas" if m.get("modulo") == "ambiental" else (
            m["tipo"][0].upper() + m["tipo"][1:])
        out.setdefault(etiqueta, []).append(m["nombre"])
    return list(out.items())


def _txt_grupo_abre(inc: Incidente, t: datetime) -> str:
    d = inc.detalle
    ms = list(d.get("miembros", {}).values())
    n, total = len(ms), max(int(d.get("total") or 0), len(ms))
    pal = _palabra((m.get("modulo") for m in ms), total)
    caido, otro_ok, _ = _caido(d)
    modulos = d.get("modulos") or {}
    cortados = Counter(m.get("modulo") for m in ms)
    if d.get("gateway") and caido:
        titulo = f"{d['gateway']} caído ({inc.tienda})"
    elif caido:
        titulo = f"{inc.tienda}: sede sin datos"
    else:
        lugar = f"{d['gateway']} ({inc.tienda})" if d.get("gateway") else inc.tienda
        titulo = f"{lugar}: {n} {_palabra(cortados, n)} se cortaron juntos"
    desdes = sorted(_dt(m["desde"]) for m in ms)
    rango = (f"desde {cuando(desdes[0], t)}" if desdes[-1] - desdes[0] <= timedelta(minutes=10)
             else f"entre {cuando(desdes[0], t)} y {cuando(desdes[-1], t)}")
    lineas = [f"🔴 {titulo}",
              f"{n} de {total} {pal} sin datos {rango} "
              f"(hace {duracion(_min(t - desdes[0]))}):"]
    lineas += [f"• {e}: {_lista(ns)}" for e, ns in _bloques(ms)]
    if otro_ok:
        sigue = " y ".join(f"{modulos[m]} {_palabra([m], modulos[m])} de {NOMBRE_MODULO[m]}"
                           for m in otro_ok)
        lineas.append(f"Siguen reportando {sigue} por el mismo gateway: el gateway "
                      "funciona; revisar la energía o el enlace de lo caído.")
    elif d.get("gateway") and caido:
        lineas.append("Revisar el gateway: su energía eléctrica o su enlace a internet.")
    return "\n".join(lineas)


def _txt_grupo_cierra(inc: Incidente, t: datetime) -> str:
    d = inc.detalle
    ms = list(d.get("miembros", {}).values())
    volvieron = [m for m in ms if m.get("volvio")]
    siguen = [d["miembros"][k] for k in d.get("siguen", []) if k in d["miembros"]]
    pal = _palabra((m.get("modulo") for m in ms), len(ms))
    cab = f"✅ {d.get('alcance')}: volvieron {len(volvieron)} de {len(ms)} {pal}"
    lineas = [cab]
    if volvieron:
        ini = min(_dt(m["desde"]) for m in volvieron)
        vueltas = sorted(_dt(m["volvio"]) for m in volvieron)
        if vueltas[-1] - vueltas[0] <= timedelta(minutes=10):
            cuando_v = f"a {cuando(vueltas[-1], t)}"
        else:
            cuando_v = f"entre {cuando(vueltas[0], t)} y {cuando(vueltas[-1], t)}"
        lineas[0] += f" ({duracion(_min(vueltas[-1] - ini))} sin datos)"
        lineas.append(f"Volvieron {cuando_v}; se habían cortado desde {cuando(ini, t)}.")
        if len(volvieron) <= 4:
            lineas += [f"• {m['nombre']}: {hhmm(_dt(m['desde']))} → {cuando(_dt(m['volvio']), t)}"
                       for m in sorted(volvieron, key=lambda m: m["desde"])]
    for m in siguen:
        lineas.append(f"Sigue sin datos: {m['nombre']} (desde {cuando(_dt(m['desde']), t)})")
    return "\n".join(lineas)


def _txt_plataforma(inc: Incidente, t: datetime, abre: bool) -> str:
    d = inc.detalle
    if not abre:
        vuelta = _dt(d.get("regreso")) or t
        return (f"✅ {inc.nombre}: volvieron los datos a {cuando(vuelta, t)} "
                f"({duracion(_min(vuelta - inc.desde))} sin datos)")
    pal = _palabra([inc.modulo], int(d.get("total") or 0))
    tiendas = ", ".join(d.get("tiendas", [])[:4])
    return (f"🔴 {inc.nombre} sin datos: {d.get('abajo')} de {d.get('total')} {pal} no "
            f"reportan desde {cuando(inc.desde, t)} ({tiendas}). Parece una caída de la "
            "plataforma o de su servidor, no de los equipos.")


def _txt_monitor(inc: Incidente, t: datetime, abre: bool) -> str:
    if not abre:
        return (f"⚙️ El monitor de avisos volvió a leer {inc.nombre} "
                f"({duracion(_min(t - inc.desde))} sin poder leerla).")
    mods = " ni de ".join(NOMBRE_MODULO.get(m, m) for m in inc.detalle.get("modulos", []))
    return (f"⚙️ El monitor de avisos no puede leer {inc.nombre} desde "
            f"{cuando(inc.desde, t)} ({inc.detalle.get('error', '')}). Mientras tanto no "
            f"hay avisos de {mods}: no significa que los equipos estén caídos.")


def _txt_unidades(incs: list, t: datetime, abre: bool) -> str:
    linea = _linea_abre if abre else _linea_cierra
    icono = ICONO[incs[0].nivel] if abre else "✅"
    if len(incs) == 1:
        extra = "🔋" if incs[0].tipo == "bateria" else ""
        return f"{icono}{extra} {linea(incs[0], t, True)}"
    lineas = [f"{icono} {incs[0].tienda}:"]
    lineas += [f"• {linea(i, t, False)}" for i in incs[:12]]
    if len(incs) > 12:
        lineas.append(f"… y {len(incs) - 12} más")
    return "\n".join(lineas)


def componer(eventos: list, t: datetime, reglas: Optional[Reglas] = None) -> list:
    """Eventos de un ciclo → mensajes (agrupados por sede; cierres sin sonido)."""
    r = reglas or REGLAS
    out: list = []
    abre = [e.inc for e in eventos if e.tipo == "abre"]
    cierra = [e.inc for e in eventos if e.tipo == "cierra" and e.avisar]
    # Primero el propio monitor: explica lo que viene después ("volví a leer…").
    for inc in cierra:
        if inc.tipo == "monitor":
            out.append(Mensaje(_txt_monitor(inc, t, False), MONITOR, True, t, "cierre"))
    for inc in abre:
        if inc.tipo == "monitor":
            out.append(Mensaje(_txt_monitor(inc, t, True), MONITOR, not en_horario(t, r), t))
        elif inc.tipo == "plataforma":
            out.append(Mensaje(_txt_plataforma(inc, t, True), ROJO, False, t))
    for _, incs in _por([i for i in abre if i.tipo == "grupo"], lambda i: i.tienda):
        out.append(Mensaje("\n\n".join(_txt_grupo_abre(i, t) for i in incs), ROJO, False, t))
    sueltos = [i for i in abre if i.tipo in ("sin_datos", "no_mide", "bateria")]
    for (nivel, _), incs in _por(sueltos, lambda i: (i.nivel, i.tienda)):
        out.append(Mensaje(_txt_unidades(incs, t, True), nivel, False, t))
    for inc in cierra:
        if inc.tipo == "plataforma":
            out.append(Mensaje(_txt_plataforma(inc, t, False), ROJO, True, t, "cierre"))
    resto = [i for i in cierra if i.tipo not in ("monitor", "plataforma")]
    for _, incs in _por(resto, lambda i: i.tienda):
        partes = [_txt_grupo_cierra(i, t) for i in incs if i.tipo == "grupo"]
        unidades = [i for i in incs if i.tipo != "grupo"]
        if unidades:
            partes.append(_txt_unidades(unidades, t, False))
        out.append(Mensaje("\n\n".join(partes), incs[0].nivel, True, t, "cierre"))
    return out


# ---------- Resumen diario ---------------------------------------------------------------

@dataclass
class DiaUnidad:
    """Cómo le fue a una unidad un día (lo arma avisos_datos.Historia.dia)."""
    unidad: Unidad
    cobertura: Optional[float] = None    # % lecturas/esperadas (None: no se evalúa)
    cortes: list = field(default_factory=list)       # [(inicio, minutos)]
    peor_bloque: Optional[float] = None  # peor bloque de 6 h con lecturas (%)
    bateria: Optional[float] = None
    snr: Optional[float] = None          # promedio del día
    snr_ref: Optional[float] = None      # promedio de sus 7 días previos
    raros: list = field(default_factory=list)        # textos
    estado_bateria: Optional[str] = None             # agua: estado distinto de "normal"


def _fin_corte(ini: datetime, minutos: int) -> str:
    fin = lima(ini) + timedelta(minutes=minutos)
    return "24:00" if (fin.hour, fin.minute) == (0, 0) and minutos else f"{fin:%H:%M}"


def txt_cortes(cortes: list) -> str:
    if not cortes:
        return ""
    fmt = lambda c: f"{hhmm(c[0])}–{_fin_corte(c[0], c[1])}"  # noqa: E731
    if len(cortes) == 1:
        return f"sin datos {fmt(cortes[0])} ({duracion(cortes[0][1])})"
    if len(cortes) == 2:
        return f"2 cortes: {fmt(cortes[0])} y {fmt(cortes[1])}"
    mayor = max(cortes, key=lambda c: (c[1], -c[0].timestamp()))
    return (f"intermitente: {len(cortes)} cortes, el más largo {fmt(mayor)} "
            f"({duracion(mayor[1])})")


def corte_largo(d: DiaUnidad, r: Reglas = REGLAS) -> bool:
    """¿Tuvo un corte que en horario habría sido 🟠? (de noche no se avisa: va aquí)."""
    c = _cad(d.unidad.cadencia)
    return any(m + c >= umbral_sin_datos(c, r) for _, m in d.cortes)


def problemas(d: DiaUnidad, r: Reglas = REGLAS) -> list:
    """Problemas del día (sin la batería, que va en su propia sección)."""
    out = []
    baja = d.cobertura is not None and d.cobertura < r.cobertura_min
    degradado = d.peor_bloque is not None and d.peor_bloque < r.degradado_pct
    if baja or degradado or (d.cobertura is not None and corte_largo(d, r)):
        texto = f"{pct(d.cobertura)} de lecturas"
        faltan = (100 - (d.cobertura or 100)) * 14.4          # minutos sin lecturas
        en_cortes = sum(m for _, m in d.cortes)
        if faltan >= 60 and en_cortes < 0.5 * faltan:
            texto += " · pierde lecturas sueltas (enlace débil)"
        if d.cortes:
            texto += f" · {txt_cortes(d.cortes)}"
        out.append(texto)
    if d.snr is not None and d.snr_ref is not None and d.snr <= d.snr_ref - r.snr_caida_db:
        out.append(f"SNR {d.snr:+.0f} dB (su semana {d.snr_ref:+.0f} dB): enlace de radio débil")
    out += list(d.raros)
    return out


def _corto(d: DiaUnidad, r: Reglas) -> str:
    """Lo esencial del día para el historial de la racha ('27%', 'SNR −12 dB')."""
    if d.cobertura is not None and (d.cobertura < r.cobertura_min or corte_largo(d, r)
                                    or (d.peor_bloque or 100) < r.degradado_pct):
        return pct(d.cobertura)
    if d.snr is not None and d.snr_ref is not None and d.snr <= d.snr_ref - r.snr_caida_db:
        return f"SNR {d.snr:+.0f} dB"
    return "datos raros"


def toca_resumen(estado: Estado, t: datetime, r: Reglas = REGLAS) -> bool:
    tl = lima(t)
    return (tl.hour * 60 + tl.minute >= r.resumen_min
            and estado.ultimo_resumen != tl.date().isoformat())


def _estado_txt(inc: Incidente, t: datetime) -> str:
    if inc.tipo in ("sin_datos", "plataforma"):
        return f"sin datos desde {cuando(inc.desde, t)}"
    if inc.tipo == "grupo":
        ms = inc.detalle.get("miembros", {}).values()
        n = sum(1 for m in ms if not m.get("volvio"))
        return f"{n} sin datos desde {cuando(inc.desde, t)}"
    if inc.tipo == "bateria":
        return f"batería {inc.detalle.get('bateria', 0):.0f} %"
    if inc.tipo == "no_mide":
        return f"no mide: {inc.detalle.get('motivo', '')}"
    if inc.tipo == "monitor":
        return f"el monitor no puede leerla desde {cuando(inc.desde, t)}"
    return ""


def lineas_abiertos(estado: Estado, t: datetime, maximo: int = 10) -> list:
    """Incidentes abiertos (no tapados por otro), del más grave al menos."""
    incs = sorted((i for i in estado.abiertos.values() if not cubierto(i, estado.abiertos)),
                  key=lambda i: (ORDEN_TIPO.get(i.tipo, 9), i.desde))
    out = []
    for i in incs[:maximo]:
        lugar = f" ({i.donde})" if i.donde and i.tipo != "grupo" else ""
        out.append(f"{ICONO[i.nivel]} {i.nombre}{lugar}: {_estado_txt(i, t)}")
    if len(incs) > maximo:
        out.append(f"… y {len(incs) - maximo} más")
    return out


def _vigilados(unidades: list) -> str:
    cuenta = Counter(u.modulo for u in unidades)
    partes = []
    for mod, extra in (("energia", " de energía"), ("ambiental", ""), ("agua", " de agua")):
        n = cuenta.get(mod)
        if n:
            sing, plur = PALABRA[mod]
            partes.append(f"{n} {sing if n == 1 else plur}{extra}")
    if not partes:
        return "nada"
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


def resumen_diario(estado: Estado, dias: list, fecha: date, t: datetime,
                   reglas: Optional[Reglas] = None, sin_primera: Iterable = (),
                   mantenimientos: Iterable = (), vigiladas: Optional[list] = None) -> list:
    """Resumen 🟡 del día `fecha` (+ 🟠 de las unidades que llevan 3 días con problemas).

    dias: [DiaUnidad]; sin_primera: [(Unidad, alta)]; mantenimientos: los que
    tocaron el día (sus unidades no cuentan); vigiladas: [Unidad] de ahora.
    Actualiza las rachas y marca el resumen de hoy como hecho.
    """
    r = reglas or REGLAS
    mants = list(mantenimientos)
    hoy = lima(t).date()
    en_mant = lambda u: any(cubre_unidad(m, u) for m in mants)  # noqa: E731
    con = []
    for d in dias:
        if en_mant(d.unidad):
            continue
        p = problemas(d, r)
        if p:
            con.append((d, p))
    con.sort(key=lambda x: (x[0].cobertura if x[0].cobertura is not None else 100,
                            x[0].unidad.tienda, x[0].unidad.nombre))

    # Rachas: días seguidos con problemas → 🟠 al llegar a `racha_dias`.
    ayer, hoy_txt = (fecha - timedelta(days=1)).isoformat(), fecha.isoformat()
    abiertos_avisados = {k for i in estado.abiertos.values() if i.avisado
                         and i.tipo in ("sin_datos", "grupo", "plataforma", "bateria")
                         for k in i.unidades}
    rachas, escalar = {}, []
    for d, p in con:
        k = d.unidad.clave
        prev = estado.rachas.get(k) or {}
        if prev.get("ultimo") == hoy_txt:
            rachas[k] = prev                       # el mismo día otra vez: no suma
            continue
        dia_txt = f"{DIAS_CORTO[fecha.weekday()]} {_corto(d, r)}"
        if prev.get("ultimo") == ayer:
            n, hist, escalado = int(prev.get("dias") or 0) + 1, list(prev.get("hist") or []), \
                bool(prev.get("escalado"))
        else:
            n, hist, escalado = 1, [], False
        hist = (hist + [dia_txt])[-7:]
        if n >= r.racha_dias and not escalado:
            escalado = True
            if k not in abiertos_avisados:
                escalar.append((d, n, hist))
        rachas[k] = {"dias": n, "hist": hist, "ultimo": hoy_txt, "escalado": escalado}
    estado.rachas = rachas

    vig = vigiladas if vigiladas is not None else [d.unidad for d in dias]
    lineas = [f"🟡 Resumen ZEIA de ayer, {fecha_larga(fecha)}",
              f"Vigilados: {_vigilados(vig)}."]
    abiertos = lineas_abiertos(estado, t)
    if abiertos:
        n_abiertos = sum(1 for i in estado.abiertos.values() if not cubierto(i, estado.abiertos))
        lineas += ["", f"Abiertos ahora ({n_abiertos}):"] + abiertos
    if con:
        lineas += ["", f"Ayer con problemas ({len(con)} de {len(dias)}):"]
        for d, p in con[:r.max_lineas]:
            icono = ICONO_MODULO.get(d.unidad.modulo, "")
            lineas.append(f"• {icono} {d.unidad.nombre} ({d.unidad.donde}): {' · '.join(p)}")
        if len(con) > r.max_lineas:
            lineas.append(f"… y {len(con) - r.max_lineas} más")
    bajas = sorted((d for d in dias if not en_mant(d.unidad) and (
        (d.bateria is not None and d.bateria < r.bateria_baja) or d.estado_bateria)),
        key=lambda d: d.bateria if d.bateria is not None else -1)
    if bajas:
        txt = [f"{d.unidad.nombre} {d.bateria:.0f} %" if d.bateria is not None
               else f"{d.unidad.nombre} ({d.estado_bateria})" for d in bajas]
        lineas += ["", f"🔋 Batería baja (<{r.bateria_baja:.0f} %): " + ", ".join(txt)]
    sp = list(sin_primera)
    claves = sorted(u.clave for u, _ in sp)
    if sp and (claves != sorted(estado.sin_primera) or hoy.weekday() == 0):
        txt = [f"{u.nombre} ({u.donde}, alta {a.day:02d}-{MESES[a.month - 1]})" if a
               else f"{u.nombre} ({u.donde})" for u, a in sp[:6]]
        lineas += ["", f"Sin primera lectura ({len(sp)}): " + "; ".join(txt)
                   + (f"; … y {len(sp) - 6} más" if len(sp) > 6 else "")]
    estado.sin_primera = claves
    if mants:
        lineas += ["", "🛠 En mantenimiento: " + ", ".join(sorted({m.alcance for m in mants}))]
    if not con:
        lineas += ["", f"✅ Ayer todo bien: {len(dias)} reportaron ≥ {r.cobertura_min:.0f} % "
                       "sin cortes largos."]
    else:
        lineas += ["", "Lo que no aparece estuvo bien."]
    out = [Mensaje("\n".join(lineas), AMARILLO, True, t, "resumen")]
    for d, n, hist in escalar:
        out.append(Mensaje(
            f"🟠 {ICONO_MODULO.get(d.unidad.modulo, '')} {d.unidad.nombre} ({d.unidad.donde}) "
            f"lleva {n} días seguidos con problemas: {' · '.join(hist[-n:])}. "
            "Conviene revisarlo en sitio.", NARANJA, False, t, "escalado"))
    estado.ultimo_resumen = hoy.isoformat()
    return out
