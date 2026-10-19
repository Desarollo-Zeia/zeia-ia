"""Índice de navegación de la webapp ZEIA (:8000).

Responde "¿qué páginas hay, para qué sirve cada una y de dónde sacan los
datos?". Es la ÚNICA fuente del catálogo; lo usan, vía `GET /api/indice`
(= `construir()`):

- la página de inicio `/` (web/static/inicio.html),
- el menú "☰ Índice" de todas las páginas (web/static/nav.js),
- y el mensaje de arranque de `python webapp.py` (`paginas_internas()`).

Al agregar una página (o un PDF) a webapp.py hay que sumarla a PAGINAS: si
no, tests/test_indice.py falla y la ruta aparece en el inicio como
"sin describir".

Nunca incluir credenciales: de cada base solo se muestra host:puerto y modo.
"""
from __future__ import annotations

import json
import re
import socket
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
ANALISIS = ROOT / "analisis"

RUTA_INDICE = "/"
# Rutas que agrega FastAPI solo (documentación automática): no son nuestras.
RUTAS_FASTAPI = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

MESES = ["ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic"]

SECCIONES = [
    {"id": "ia", "titulo": "Preguntar a la IA",
     "descripcion": "Respuestas en español sobre los datos, con tablas y "
                    "gráficos."},
    {"id": "vivo", "titulo": "Ver qué está fallando ahora",
     "descripcion": "Llegada de lecturas de HOY, en tiempo real."},
    {"id": "reportes", "titulo": "Reportes de la semana",
     "descripcion": "Porcentaje de lecturas recibidas por día, para los "
                    "jefes."},
    {"id": "historico", "titulo": "Investigar huecos pasados",
     "descripcion": "Histórico de pérdidas de datos por punto y día."},
    {"id": "tecnico", "titulo": "Técnico",
     "descripcion": "Documentación de la API y maquetas."},
]

# Archivos de analisis/ que alimentan algunas páginas: esas páginas NO
# consultan la base, muestran lo que haya en el último export.
#   hasta_json   → (archivo, claves) donde el export guarda su último día
#   hasta_nombre → el último día está en el nombre: ..._a_AAAA-MM-DD.csv
EXPORTS = {
    "energia": {
        "titulo": "Export de energía",
        "archivos": ["cobertura_diaria.csv", "cobertura_resumen.json"],
        "hasta_json": ("cobertura_resumen.json", ["meta", "global_max_day"]),
        "comando": "python scripts/analisis_huecos.py --export",
    },
    "ambiental": {
        "titulo": "Export ambiental",
        "archivos": ["huecos_ambiental_salas.json",
                     "cobertura_diaria_ambiental_salas.json"],
        "hasta_json": ("huecos_ambiental_salas.json", ["global_max_day"]),
        "comando": "python scripts/analisis_huecos_ambiental.py --export",
    },
    "eventos": {
        "titulo": "CSV de eventos y huecos (formato antiguo)",
        # El más nuevo de cada patrón, sin los de ambiental (= webapp._latest_csv)
        "archivos": ["eventos_*.csv", "huecos_*.csv"],
        "hasta_nombre": True,
        "comando": None,
        "nota": "El script actual ya no genera estos archivos: se quedan con "
                "los datos de cuando se crearon.",
    },
}

# Catálogo de páginas. Campos:
#   ruta       ruta en ESTA app (None si es de otra app → "externa")
#   resumen    una línea (también va en el menú ☰)
#   detalle    qué se ve y cómo se usa (solo en el inicio)
#   cuando     para qué conviene usarla
#   variantes  enlaces a versiones de la misma página (?parámetros)
#   pdf        descargas en PDF de la página
#   datos      bases que consulta en vivo y/o exports de analisis/ que lee
#   sin_menu   True = la página no lleva el botón ☰ (Swagger, maquetas)
#   externa    página de OTRA app (puerto propio): se enlaza, no se sirve aquí
PAGINAS = [
    {
        "id": "chat", "seccion": "ia", "ruta": "/chat",
        "titulo": "Asistente IA",
        "resumen": "Preguntas en español sobre energía o ambiental; responde "
                   "con tablas y gráficos.",
        "detalle": [
            "Al entrar eliges el módulo (Energía o Ambiental) y el perfil: "
            "Analista (detalle y SQL) o Gerente (visión general, costos en "
            "soles).",
            "Pestaña Dashboard: tarjetas con preguntas predefinidas (la "
            "primera vez tarda 1–2 min; después quedan guardadas en el "
            "navegador). Pestaña Conversar: chat libre, también por voz 🎙 "
            "si ElevenLabs está configurado.",
            "También lee las facturas PDF de la carpeta del proyecto. "
            "Todavía no conoce los medidores de agua.",
        ],
        "cuando": "Consumos, costos, alertas, comparaciones entre sedes y "
                  "facturas.",
        "datos": {"bases": ["energia", "ambiental"]},
    },
    {
        "id": "monitor", "seccion": "vivo", "ruta": "/monitor",
        "titulo": "Monitor en vivo",
        "resumen": "Qué punto o sensor dejó de enviar datos HOY, minuto a "
                   "minuto (verde = llegó, rojo = faltó).",
        "detalle": [
            "Pantalla tipo TV con todos los puntos activos; se actualiza "
            "sola cada 20 s, no hace falta tocar nada.",
            "«Resumen»: tiendas → puntos con semáforo (última lectura y "
            "ritmo) e incidentes abiertos; clic en un punto para ver su "
            "minuto a minuto.",
            "Tiene sentido contra producción: si la base es una copia local "
            "vieja, avisa con un banner de «fuente congelada».",
        ],
        "cuando": "Detectar en el momento un equipo, sala o gateway caído.",
        "variantes": [
            {"etiqueta": "Energía", "url": "/monitor"},
            {"etiqueta": "Ambiental", "url": "/monitor?base=ambiental"},
            {"etiqueta": "Resumen por tienda", "url": "/monitor?vista=resumen"},
        ],
        "datos": {"bases": ["energia", "ambiental"]},
    },
    {
        "id": "reporte-semanal", "seccion": "reportes",
        "ruta": "/reporte-semanal",
        "titulo": "Reporte semanal de cobertura (web)",
        "resumen": "% de lecturas recibidas por equipo/sala y día (energía + "
                   "ambiental), con descarga en PDF.",
        "detalle": [
            "Por defecto muestra la última semana completa (lunes a "
            "domingo); se cambia con Desde/Hasta.",
            "Lista solo lo que tuvo problemas (lo que estuvo al 100% va "
            "resumido en una línea) y deja fuera a las empresas sin medición "
            "activa.",
            "No consulta la base: lee los exports de analisis/. Si no llegan "
            "a la semana pedida, el reporte sale vacío o marca equipos como "
            "caídos sin estarlo.",
        ],
        "cuando": "Revisión rápida en el navegador. El PDF semanal para el "
                  "jefe (formato actual, desde el 11-oct) sale de "
                  "scripts/reporte_conectividad.py: ver «Desde la terminal».",
        "pdf": [{"etiqueta": "PDF de la semana pasada",
                 "url": "/reporte-semanal/pdf"}],
        "datos": {"exports": ["energia", "ambiental"]},
    },
    {
        "id": "reporte-gateways", "seccion": "reportes",
        "ruta": "/reporte-gateways",
        "titulo": "Reporte Sanna por gateway",
        "resumen": "Sanna San Borja agrupado Empresa → Gateway → sala o "
                   "equipo, con % por día y promedios.",
        "detalle": [
            "Formato del boceto del jefe, en dos versiones: salas (sensores "
            "ambientales) y energía (medidores).",
            "Los porcentajes salen de los exports de analisis/; la base solo "
            "se consulta para ubicar cada equipo en su gateway.",
        ],
        "cuando": "Ver si una falla es de un gateway entero o de un equipo.",
        "variantes": [
            {"etiqueta": "Salas (ambiental)", "url": "/reporte-gateways"},
            {"etiqueta": "Energía", "url": "/reporte-gateways?alcance=energia"},
        ],
        "pdf": [
            {"etiqueta": "PDF salas", "url": "/reporte-gateways/pdf"},
            {"etiqueta": "PDF energía", "url": "/reporte-gateways-energia/pdf"},
        ],
        "datos": {"exports": ["ambiental", "energia"],
                  "bases": ["energia", "ambiental"]},
    },
    {
        "id": "gaps", "seccion": "historico", "ruta": "/gaps",
        "titulo": "Huecos de lectura (energía)",
        "resumen": "Histórico de pérdidas de datos por punto de energía: "
                   "cobertura por día, minuto a minuto y eventos.",
        "detalle": [
            "«Cobertura diaria»: cada punto × día en verde/ámbar/rojo (lee el "
            "export de energía).",
            "«Minuto a minuto»: qué minutos llegaron, hasta 10 días y 40 "
            "puntos a la vez (consulta la base).",
            "«Eventos agrupados» y «Huecos individuales»: leen CSV con el "
            "formato antiguo, que ya no se regeneran.",
        ],
        "cuando": "Investigar desde cuándo y cuánto falló un punto.",
        "datos": {"exports": ["energia", "eventos"], "bases": ["energia"]},
    },
    {
        "id": "gaps-ambiental", "seccion": "historico", "ruta": None,
        "externa": {"puerto": 8001, "ruta": "/gaps",
                    "comando": "python webapp_ambiental.py"},
        "titulo": "Huecos de lectura (ambiental)",
        "resumen": "Lo mismo que «Huecos de lectura» para los sensores "
                   "ambientales (base valhalladb).",
        "detalle": [
            "Es OTRA app (puerto 8001): hay que levantarla aparte con "
            "«python webapp_ambiental.py». El enlace solo funciona mientras "
            "esté corriendo.",
        ],
        "cuando": "Investigar huecos de una sala o sensor ambiental.",
        "datos": {"exports": ["ambiental"], "bases": ["ambiental"]},
    },
    {
        "id": "api-docs", "seccion": "tecnico", "ruta": "/docs",
        "titulo": "API (Swagger)",
        "resumen": "Lista automática de los endpoints /api/… con sus "
                   "parámetros; se pueden probar desde ahí.",
        "sin_menu": True,
    },
    {
        "id": "mockup-agua", "seccion": "tecnico",
        "ruta": "/static/mockup_agua.html",
        "titulo": "Maqueta: monitoreo de agua",
        "resumen": "Propuesta de diseño con datos SIMULADOS: no lee la base.",
        "sin_menu": True,
    },
]

# Comandos de terminal más usados (PowerShell, en la carpeta del proyecto y
# con el venv activo). "archivo": solo se muestra si existe en esta PC.
COMANDOS = [
    {"titulo": "Levantar esta web",
     "comando": "python webapp.py",
     "nota": "Usa la base que diga .env. Queda en http://localhost:8000."},
    {"titulo": "Levantar esta web leyendo producción (sin tocar .env)",
     "comando": r".\scripts\webapp_en_vivo.cmd",
     "archivo": "scripts/webapp_en_vivo.cmd",
     "nota": r"Abre los túneles SSH sola; el log queda en logs\webapp.out.log."},
    {"titulo": "PDF semanal de conectividad (el que va al jefe)",
     "comando": "python scripts/reporte_conectividad.py",
     "archivo": "scripts/reporte_conectividad.py",
     "nota": "Semana pasada, todas las empresas → carpeta Descargas. Otra "
             "semana: --desde AAAA-MM-DD --hasta AAAA-MM-DD; una empresa: "
             "--empresa Sanna. Aborta si la base no llega al domingo."},
    {"titulo": "Regenerar el export de energía",
     "comando": EXPORTS["energia"]["comando"],
     "archivo": "scripts/analisis_huecos.py",
     "nota": "Actualiza Huecos (energía), el Reporte semanal y Gateways "
             "energía."},
    {"titulo": "Regenerar el export ambiental",
     "comando": EXPORTS["ambiental"]["comando"],
     "archivo": "scripts/analisis_huecos_ambiental.py",
     "nota": "Actualiza el Reporte semanal, Gateways salas y Huecos "
             "(ambiental)."},
    {"titulo": "Copiar producción a la base local",
     "comando": "bash scripts/sync_db.sh energia full",
     "archivo": "scripts/sync_db.sh",
     "nota": "Cambiar «energia» por «ambiental» para la otra base. Con Git "
             "Bash; hace dump + restore y verifica."},
]


# --- Frescura de los exports ---------------------------------------------------

def ultimo_domingo(hoy: date) -> date:
    """Domingo de la última semana completa (lun–dom) antes de `hoy`."""
    return hoy - timedelta(days=hoy.weekday() + 1)


def _fecha(d: date, hoy: date) -> str:
    txt = f"{d.day:02d}-{MESES[d.month - 1]}"
    return txt if d.year == hoy.year else f"{txt}-{d.year}"


def _hace(d: date, hoy: date) -> str:
    n = (hoy - d).days
    return "hoy" if n <= 0 else "ayer" if n == 1 else f"hace {n} días"


def _rango(d1: date, d2: date) -> str:
    if d1.month == d2.month:
        return f"{d1.day:02d}–{d2.day:02d} {MESES[d2.month - 1]}"
    return (f"{d1.day:02d} {MESES[d1.month - 1]} – "
            f"{d2.day:02d} {MESES[d2.month - 1]}")


def _archivo(patron: str, carpeta: Path) -> Optional[Path]:
    """El archivo del patrón; con comodín, el más nuevo (sin los ambientales)."""
    if "*" not in patron:
        p = carpeta / patron
        return p if p.is_file() else None
    files = [p for p in carpeta.glob(patron)
             if p.is_file() and "ambiental" not in p.name]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


_FIN_EN_NOMBRE = re.compile(r"_a_(\d{4}-\d{2}-\d{2})")


def _hasta(cfg: dict, archivos: list, carpeta: Path) -> Optional[date]:
    """Último día con datos dentro del export (None si no se puede leer)."""
    try:
        if cfg.get("hasta_json"):
            nombre, claves = cfg["hasta_json"]
            v = json.loads((carpeta / nombre).read_text(encoding="utf-8"))
            for k in claves:
                v = v.get(k) if isinstance(v, dict) else None
            return date.fromisoformat(str(v)[:10]) if v else None
        if cfg.get("hasta_nombre"):
            fechas = []
            for p in archivos:
                m = _FIN_EN_NOMBRE.search(p.name)
                if m:
                    fechas.append(date.fromisoformat(m.group(1)))
            return min(fechas) if fechas else None
    except (OSError, ValueError, TypeError):
        return None
    return None


def estado_export(export_id: str, carpeta: Path = ANALISIS,
                  hoy: Optional[date] = None) -> dict:
    """Qué tan fresco está un export: estado ok | viejo | falta + texto.

    "ok" = sus datos llegan al menos al domingo de la semana pasada (lo que
    piden los reportes semanales por defecto).
    """
    cfg = EXPORTS[export_id]
    hoy = hoy or date.today()
    encontrados, faltan = [], []
    for patron in cfg["archivos"]:
        p = _archivo(patron, carpeta)
        if p:
            encontrados.append(p)
        else:
            faltan.append(patron)
    out = {"id": export_id, "titulo": cfg["titulo"],
           "comando": cfg.get("comando"), "nota": cfg.get("nota"),
           "archivos": [p.name for p in encontrados]}
    if faltan:
        out.update(estado="falta", generado=None, hasta=None,
                   texto=f"Falta {', '.join(faltan)} en analisis/.")
        return out
    generado = datetime.fromtimestamp(
        min(p.stat().st_mtime for p in encontrados))
    hasta = _hasta(cfg, encontrados, carpeta)
    domingo = ultimo_domingo(hoy)
    gen_txt = (f"generado el {_fecha(generado.date(), hoy)} "
               f"{generado:%H:%M} ({_hace(generado.date(), hoy)})")
    if hasta is None:
        estado = "viejo"
        texto = f"No se pudo leer hasta qué día llega; {gen_txt}."
    else:
        estado = "ok" if hasta >= domingo else "viejo"
        texto = f"Datos hasta el {_fecha(hasta, hoy)}, {gen_txt}. " + (
            "Cubre la semana pasada." if estado == "ok" else
            "No cubre la semana pasada "
            f"({_rango(domingo - timedelta(days=6), domingo)}).")
    out.update(estado=estado, generado=generado.isoformat(timespec="minutes"),
               hasta=hasta.isoformat() if hasta else None, texto=texto)
    return out


# --- Bases y rutas -------------------------------------------------------------

def describir_bases(dbs: Iterable) -> list:
    """Cómo está conectada cada base en ESTE proceso (sin credenciales)."""
    out = []
    for c in dbs:
        if c.use_ssh_tunnel:
            modo, texto = "produccion", "Producción en vivo (túnel SSH)"
        elif c.host in ("127.0.0.1", "localhost", "::1"):
            modo, texto = "local", "Copia local en esta PC (sync_db.sh)"
        else:
            modo, texto = "remota", "Servidor remoto"
        out.append({"id": c.name, "label": c.label, "base": c.dbname,
                    "destino": f"{c.host}:{c.port}", "modo": modo,
                    "texto": texto})
    return out


def tipo_ruta(ruta: str, metodos: list) -> str:
    """pagina | pdf | api | fastapi (convención de rutas de webapp.py)."""
    if ruta in RUTAS_FASTAPI:
        return "fastapi"
    if ruta.startswith("/api/"):
        return "api"
    if ruta.endswith("/pdf"):
        return "pdf"
    return "pagina" if "GET" in metodos else "api"


_ORDEN_TIPO = {"pagina": 0, "pdf": 1, "api": 2, "fastapi": 3}


def listar_rutas(rutas_app: Iterable) -> list:
    """Todas las rutas HTTP de la app (app.routes) con su tipo y docstring."""
    out = []
    for r in rutas_app:
        ruta = getattr(r, "path", None)
        metodos = sorted(set(getattr(r, "methods", None) or ())
                         - {"HEAD", "OPTIONS"})
        if not ruta or not metodos:   # p. ej. el Mount de /static
            continue
        doc = (getattr(getattr(r, "endpoint", None), "__doc__", None)
               or "").strip()
        out.append({"ruta": ruta, "metodos": metodos,
                    "tipo": tipo_ruta(ruta, metodos),
                    "descripcion": doc.splitlines()[0].strip() if doc else ""})
    out.sort(key=lambda x: (_ORDEN_TIPO[x["tipo"]], x["ruta"]))
    return out


def rutas_sin_describir(rutas: list) -> list:
    """Páginas y PDF de la app que no están en PAGINAS (deberían estarlo)."""
    paginas = {RUTA_INDICE} | {p["ruta"] for p in PAGINAS if p.get("ruta")}
    pdfs = {urlsplit(x["url"]).path for p in PAGINAS for x in p.get("pdf", [])}
    return [r for r in rutas
            if (r["tipo"] == "pagina" and r["ruta"] not in paginas)
            or (r["tipo"] == "pdf" and r["ruta"] not in pdfs)]


def puerto_abierto(puerto: int, host: str = "127.0.0.1",
                   timeout: float = 0.2) -> bool:
    """¿Hay algo escuchando en ese puerto de esta PC? (apps externas)."""
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def paginas_internas() -> list:
    """Páginas propias de esta app (para el mensaje de arranque)."""
    return [p for p in PAGINAS if p.get("ruta") and p["seccion"] != "tecnico"]


def construir(rutas_app: Iterable, dbs: Iterable,
              base_url: str = "http://localhost:8000/",
              carpeta: Path = ANALISIS, hoy: Optional[date] = None) -> dict:
    """Todo lo que muestran el inicio y el menú ☰ (respuesta de /api/indice)."""
    hoy = hoy or date.today()
    host = urlsplit(base_url).hostname or "localhost"
    if ":" in host:          # IPv6 literal
        host = f"[{host}]"
    paginas = []
    for p in PAGINAS:
        q = dict(p)
        ext = p.get("externa")
        if ext:
            q["url"] = f"http://{host}:{ext['puerto']}{ext['ruta']}"
            q["corriendo"] = puerto_abierto(ext["puerto"])
        else:
            q["url"] = p["ruta"]
        paginas.append(q)
    rutas = listar_rutas(rutas_app)
    return {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "bases": describir_bases(dbs),
        "secciones": [{**s, "paginas": [p for p in paginas
                                         if p["seccion"] == s["id"]]}
                      for s in SECCIONES],
        "exports": {k: estado_export(k, carpeta, hoy) for k in EXPORTS},
        "comandos": [c for c in COMANDOS
                     if not c.get("archivo") or (ROOT / c["archivo"]).exists()],
        "rutas": rutas,
        "sin_describir": rutas_sin_describir(rutas),
    }
