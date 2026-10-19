"""Pruebas del índice de navegación (sin base de datos).

    python -m unittest tests.test_indice -v

La principal: toda página o PDF que se agregue a webapp.py tiene que estar
descrita en src/indice.py (PAGINAS). Si falla, agregarla ahí.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
import warnings
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if not (ROOT / ".env").exists():   # clon sin .env: config.py exige la clave
    os.environ.setdefault("OPENROUTER_API_KEY", "sin-uso-en-pruebas")

with warnings.catch_warnings():
    warnings.simplefilter("ignore")  # aviso de starlette sobre httpx
    from fastapi.testclient import TestClient

import webapp  # noqa: E402
from src import config, indice  # noqa: E402

STATIC = ROOT / "web" / "static"


def _rutas_app() -> list:
    return indice.listar_rutas(webapp.app.routes)


def _ruta(path: str, metodos=("GET",)):
    """Ruta falsa con la forma de las de FastAPI (path/methods/endpoint)."""
    return SimpleNamespace(path=path, methods=set(metodos), endpoint=lambda: None)


class CatalogoCompleto(unittest.TestCase):
    """Que el índice no se quede atrás cuando se agregan páginas."""

    def test_toda_pagina_y_pdf_de_la_app_esta_descrita(self):
        faltan = indice.rutas_sin_describir(_rutas_app())
        self.assertEqual(faltan, [], "Agregar a src/indice.py (PAGINAS): "
                         + ", ".join(r["ruta"] for r in faltan))

    def test_detecta_una_pagina_nueva_sin_describir(self):
        rutas = indice.listar_rutas([*webapp.app.routes,
                                     _ruta("/pagina-nueva", ("GET", "HEAD")),
                                     _ruta("/pagina-nueva/pdf"),
                                     _ruta("/api/algo-nuevo"),
                                     _ruta("/accion", ("POST",))])
        self.assertEqual([r["ruta"] for r in indice.rutas_sin_describir(rutas)],
                         ["/pagina-nueva", "/pagina-nueva/pdf"])

    def test_enlaces_del_indice_existen(self):
        rutas = {r["ruta"] for r in _rutas_app()}
        for p in indice.PAGINAS:
            urls = [x["url"] for x in p.get("variantes", []) + p.get("pdf", [])]
            if p.get("ruta"):
                urls.append(p["ruta"])
            for url in urls:
                ruta = urlsplit(url).path
                with self.subTest(pagina=p["id"], url=url):
                    if ruta.startswith("/static/"):
                        self.assertTrue((STATIC / ruta[len("/static/"):]).is_file())
                    else:
                        self.assertIn(ruta, rutas)

    def test_campos_coherentes(self):
        ids = [p["id"] for p in indice.PAGINAS]
        self.assertEqual(len(ids), len(set(ids)), "ids repetidos")
        secciones = {s["id"] for s in indice.SECCIONES}
        for p in indice.PAGINAS:
            with self.subTest(pagina=p["id"]):
                self.assertIn(p["seccion"], secciones)
                self.assertTrue(p["titulo"] and p["resumen"])
                self.assertNotEqual(bool(p.get("ruta")), bool(p.get("externa")),
                                    "o ruta propia o de otra app, no ambas")
                datos = p.get("datos", {})
                for b in datos.get("bases", []):
                    self.assertIn(b, config.DBS)
                for e in datos.get("exports", []):
                    self.assertIn(e, indice.EXPORTS)

    def test_scripts_de_los_exports_existen(self):
        for e in indice.EXPORTS.values():
            if e["comando"]:
                script = e["comando"].split()[1]
                self.assertTrue((ROOT / script).is_file(), script)

    def test_tipo_ruta(self):
        self.assertEqual(indice.tipo_ruta("/monitor", ["GET"]), "pagina")
        self.assertEqual(indice.tipo_ruta("/reporte-semanal/pdf", ["GET"]), "pdf")
        self.assertEqual(indice.tipo_ruta("/api/chat", ["POST"]), "api")
        self.assertEqual(indice.tipo_ruta("/accion", ["POST"]), "api")
        self.assertEqual(indice.tipo_ruta("/docs", ["GET"]), "fastapi")


class Paginas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(webapp.app)

    def test_inicio_en_la_raiz_y_chat_en_chat(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ZEIA · Índice de páginas", r.text)
        self.assertIn("no-cache", r.headers.get("cache-control", ""))
        r = self.client.get("/chat")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ZEIA · Asistente de monitoreo", r.text)
        self.assertIn("no-cache", r.headers.get("cache-control", ""))

    def test_cada_pagina_trae_el_menu(self):
        for p in indice.PAGINAS:
            if not p.get("ruta") or p.get("sin_menu"):
                continue
            with self.subTest(ruta=p["ruta"]):
                r = self.client.get(p["ruta"])
                self.assertEqual(r.status_code, 200)
                self.assertIn('src="/static/nav.js', r.text)
                self.assertIn("data-zeia-nav", r.text)
        self.assertEqual(self.client.get("/static/nav.js").status_code, 200)

    def test_menu_no_se_imprime(self):
        js = (STATIC / "nav.js").read_text(encoding="utf-8")
        self.assertIn("@media print{.zeia-nav-btn,dialog.zeia-nav"
                      "{display:none!important}}", js)

    def test_pestanas_del_chat_tienen_destino(self):
        # El chat le pone su manejador de pestañas a todo .tab y busca
        # #tab-<data-tab>: un .tab sin data-tab (los links que había a
        # Monitor/Huecos) daba error en la consola al hacer clic.
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        tabs = re.findall(r'<[a-z]+\b[^>]*class="tab(?: active)?"[^>]*>', html)
        self.assertTrue(tabs)
        for t in tabs:
            self.assertIn("data-tab=", t)

    def test_api_indice(self):
        r = self.client.get("/api/indice")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["sin_describir"], [])
        self.assertEqual({b["id"] for b in d["bases"]}, set(config.DBS))
        self.assertEqual(set(d["exports"]), set(indice.EXPORTS))
        paginas = [p for s in d["secciones"] for p in s["paginas"]]
        self.assertEqual(len(paginas), len(indice.PAGINAS))
        self.assertTrue(all(p["url"] for p in paginas))
        ext = next(p for p in paginas if p.get("externa"))
        self.assertTrue(ext["url"].startswith("http://testserver:8001/"))
        self.assertIn(ext["corriendo"], (True, False))

    def test_api_indice_no_expone_credenciales(self):
        r = self.client.get("/api/indice")
        for db in config.DBS.values():
            if len(db.password) >= 6:
                self.assertNotIn(db.password, r.text)
        for b in r.json()["bases"]:
            self.assertFalse({"password", "user", "ssh_key"} & set(b), b)


class Frescura(unittest.TestCase):
    HOY = date(2026, 10, 19)   # lunes → la semana pasada es 12–18 oct

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _escribir(self, nombre, contenido="x", dia=date(2026, 10, 9)):
        p = self.dir / nombre
        p.write_text(contenido, encoding="utf-8")
        ts = datetime(dia.year, dia.month, dia.day, 12, 0).timestamp()
        os.utime(p, (ts, ts))
        return p

    def _energia(self, hasta: str) -> dict:
        self._escribir("cobertura_diaria.csv")
        self._escribir("cobertura_resumen.json",
                       json.dumps({"meta": {"global_max_day": hasta}}))
        return indice.estado_export("energia", self.dir, self.HOY)

    def test_ultimo_domingo(self):
        self.assertEqual(indice.ultimo_domingo(date(2026, 10, 19)), date(2026, 10, 18))
        self.assertEqual(indice.ultimo_domingo(date(2026, 10, 18)), date(2026, 10, 11))
        self.assertEqual(indice.ultimo_domingo(date(2026, 10, 22)), date(2026, 10, 18))

    def test_misma_semana_que_el_reporte_semanal(self):
        self.assertEqual(indice.ultimo_domingo(date.today()),
                         webapp._rango_semanal(None, None)[1])

    def test_falta_archivo(self):
        self._escribir("cobertura_diaria.csv")
        e = indice.estado_export("energia", self.dir, self.HOY)
        self.assertEqual(e["estado"], "falta")
        self.assertIn("cobertura_resumen.json", e["texto"])

    def test_al_dia(self):
        e = self._energia("2026-10-19")
        self.assertEqual((e["estado"], e["hasta"]), ("ok", "2026-10-19"))
        self.assertIn("Cubre la semana pasada", e["texto"])

    def test_desactualizado(self):
        e = self._energia("2026-10-09")
        self.assertEqual(e["estado"], "viejo")
        self.assertEqual(e["texto"],
                         "Datos hasta el 09-oct, generado el 09-oct 12:00 "
                         "(hace 10 días). No cubre la semana pasada (12–18 oct).")

    def test_limite_es_el_domingo(self):
        self.assertEqual(self._energia("2026-10-18")["estado"], "ok")
        self.assertEqual(self._energia("2026-10-17")["estado"], "viejo")

    def test_json_ilegible(self):
        self._escribir("cobertura_diaria.csv")
        self._escribir("cobertura_resumen.json", "{roto")
        e = indice.estado_export("energia", self.dir, self.HOY)
        self.assertEqual(e["estado"], "viejo")
        self.assertIn("No se pudo leer", e["texto"])

    def test_csv_antiguos_el_mas_nuevo_y_fecha_del_nombre(self):
        for fin, dia in (("2026-08-19", date(2026, 8, 19)),
                         ("2026-08-21", date(2026, 8, 21))):
            self._escribir(f"eventos_2026-08-01_a_{fin}.csv", dia=dia)
            self._escribir(f"huecos_2026-08-01_a_{fin}.csv", dia=dia)
        self._escribir("huecos_ambiental.csv", dia=date(2026, 10, 1))  # no cuenta
        e = indice.estado_export("eventos", self.dir, self.HOY)
        self.assertEqual(e["hasta"], "2026-08-21")
        self.assertEqual(sorted(e["archivos"]),
                         ["eventos_2026-08-01_a_2026-08-21.csv",
                          "huecos_2026-08-01_a_2026-08-21.csv"])
        self.assertEqual(e["estado"], "viejo")


class Bases(unittest.TestCase):
    def _db(self, **kw):
        datos = dict(name="energia", label="Energía", host="127.0.0.1",
                     port=5432, dbname="energy", use_ssh_tunnel=False,
                     user="usuario-db", password="clave-secreta-123")
        datos.update(kw)
        return SimpleNamespace(**datos)

    def test_modos(self):
        out = indice.describir_bases([
            self._db(),
            self._db(name="ambiental", port=5435, use_ssh_tunnel=True),
            self._db(host="10.0.0.5")])
        self.assertEqual([b["modo"] for b in out], ["local", "produccion", "remota"])
        self.assertEqual(out[1]["destino"], "127.0.0.1:5435")

    def test_no_expone_credenciales(self):
        d = indice.construir(webapp.app.routes, [self._db()],
                             hoy=date(2026, 10, 19))
        texto = json.dumps(d, ensure_ascii=False)
        self.assertNotIn("clave-secreta-123", texto)
        self.assertNotIn("usuario-db", texto)


if __name__ == "__main__":
    unittest.main()
