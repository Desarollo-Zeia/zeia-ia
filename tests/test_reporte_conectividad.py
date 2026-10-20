"""Pruebas del reporte semanal de conectividad (sin base de datos).

    python -m unittest tests.test_reporte_conectividad -v
"""
from __future__ import annotations

import importlib.util
import unittest
from collections import namedtuple
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

RUTA = Path(__file__).resolve().parent.parent / "scripts" / "reporte_conectividad.py"
_spec = importlib.util.spec_from_file_location("reporte_conectividad", RUTA)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

DIAS = [date(2026, 9, 28) + timedelta(days=i) for i in range(7)]
InvAmb = namedtuple("InvAmb", "empresa sede sala uid indicador sala_id", defaults=(None,))
InvEne = namedtuple("InvEne", "empresa sede tablero uid nombre principal")
InvAgua = namedtuple("InvAgua", "empresa sede tuberia uid nombre principal")


def _cnt(lecturas: dict) -> dict:
    """{uid: [7 conteos]} -> {uid: {día: n}}, como rc.conteos (sin filas en 0)."""
    out = {uid: {DIAS[i]: n for i, n in enumerate(v) if n} for uid, v in lecturas.items()}
    return {uid: d for uid, d in out.items() if d}


def _ambiental():
    """3 salas × 3 sensores (288/día) con fallas armadas a mano + 1 sala sin datos."""
    inv, lect = [], {}
    uid = 0
    for sala in ("Sala A", "Sala B", "Sala C"):
        for ind in ("TEMPERATURE", "CO2", "HUMIDITY"):
            uid += 1
            inv.append(InvAmb("Sanna", "San Borja", sala, uid, ind))
            lect[uid] = [288] * 7
    lect[2][0] = 200            # Sala A: solo el CO2 falla el lunes
    for u in (4, 5, 6):         # Sala B: los 3 sensores caen igual el martes
        lect[u][1] = 100
    lect[7][2] = 290            # Sala C: más lecturas que las esperadas
    inv.append(InvAmb("Sanna", "San Borja", "Sala D", 99, "CO2"))   # nunca reportó
    mod = rc.modulo_ambiental(inv, _cnt(lect), set(), DIAS)
    rc.calcular(mod, 90.0)
    return mod


def _falso_consultar(inv: dict, lect: dict, previos: dict | None = None,
                     nuevos: dict | None = None, log: list | None = None):
    """Sustituye a rc.consultar: responde según el módulo y la consulta pedida
    (inv/lect/previos/nuevos van por módulo: energia, ambiental, agua)."""
    previos, nuevos = previos or {}, nuevos or {}
    por_sql = {sql: (modulo, tipo) for modulo, d in rc.SQL.items() for tipo, sql in d.items()}

    def consultar(base, sql, **params):
        if sql not in por_sql:
            raise AssertionError(f"consulta inesperada: {sql[:40]}")
        modulo, tipo = por_sql[sql]
        if base != rc.BASE_DE[modulo]:          # el agua se consulta en la base de energía
            raise AssertionError(f"{modulo} consultado en la base {base}")
        if tipo == "inv":
            return inv.get(modulo, [])
        if tipo == "dias":
            return [(uid, DIAS[i], n) for uid, v in lect.get(modulo, {}).items()
                    if uid in params["ids"] for i, n in enumerate(v) if n]
        if log is not None:
            log.append((modulo, tipo, sorted(params["ids"])))
        fuente = previos if tipo == "previos" else nuevos
        return [(u,) for u in fuente.get(modulo, ()) if u in params["ids"]]
    return consultar


def _datos(log: list | None = None) -> dict:
    """Dos bases con empresas reales, una sin datos y dos de prueba (sin agua)."""
    inv = {
        "energia": [
            InvEne("Sanna", "San Borja", "Tablero General TG-P1", 1, "Llave general", True),
            InvEne("Oechsle", "Salaverry", "TTA-TR1", 2, "Red normal", True),
            InvEne("Burger King", "Salaverry", "Tablero General TG-1", 3, "TG-1", True),
            InvEne("TestCorp_Email", "Sede Email Test", "TG", 4, "PM-Email", True)],
        "ambiental": [
            InvAmb("Sanna", "San Borja", "Sala técnica", 10, "CO2"),
            InvAmb("Zeia", "San Isidro", "Oficina", 11, "CO2")],
    }
    lect = {"energia": {1: [1400] * 7, 2: [1400] * 7, 4: [1440] * 7},   # BK (3) sin datos
            "ambiental": {10: [287] * 7, 11: [288] * 7}}
    with mock.patch.object(rc, "consultar", _falso_consultar(inv, lect, log=log)):
        return {modulo: rc.cargar(modulo, DIAS) for modulo in rc.MODULOS}


def _scotiabank(nuevos: dict | None = None, sitio_lect: list | None = None) -> dict:
    """Energía en Jockey Plaza + agua en Jockey Plaza (cada 10 min) y en SITE
    Barranco (cada 30 min), como en la base real."""
    inv = {"energia": [InvEne("Scotiabank", "Jockey Plaza", "Tablero General TG", 232,
                              "Llave general", True)],
           "agua": [InvAgua("Scotiabank", "Jockey Plaza", "Kitchenette", 2, "Medidor general", True),
                    InvAgua("Scotiabank", "SITE Barranco", "Principal", 3, "Medidor general", True),
                    InvAgua("Zeia Demo", "Surco", "Tubo de caño cocina", 1, "puntodemo", True)]}
    lect = {"energia": {232: [1440] * 7},
            "agua": {2: [144] * 7, 3: sitio_lect or [48] * 7}}
    with mock.patch.object(rc, "consultar", _falso_consultar(inv, lect, nuevos=nuevos)):
        return {modulo: rc.cargar(modulo, DIAS) for modulo in rc.MODULOS}


class TestFormato(unittest.TestCase):
    def test_umbral_usa_el_valor_impreso(self):
        self.assertFalse(rc.bajo(89.96, 90))   # se imprime 90.0% -> no rojo
        self.assertTrue(rc.bajo(89.94, 90))    # se imprime 89.9% -> rojo
        self.assertFalse(rc.bajo(90.0, 90))

    def test_porcentaje_tope_100(self):
        self.assertEqual(rc.pct(290, 288), 100.0)
        self.assertAlmostEqual(rc.pct(1000, 1440), 69.444, places=3)

    def test_fechas(self):
        self.assertEqual(rc.semana_pasada(date(2026, 10, 11)),
                         (date(2026, 9, 28), date(2026, 10, 4)))
        self.assertEqual(rc.rango_largo(date(2026, 9, 28), date(2026, 10, 4)),
                         "Lunes 28 de septiembre al domingo 04 de octubre de 2026")


class TestAmbiental(unittest.TestCase):
    def setUp(self):
        self.mod = _ambiental()
        self.salas = {f["nombre"]: f for b in self.mod["bloques"] for f in b["filas"]}

    def test_sala_sin_datos_queda_fuera(self):
        self.assertNotIn("Sala D", self.salas)
        self.assertEqual(self.mod["excluidos"], 1)

    def test_sala_toma_su_peor_sensor(self):
        a = self.salas["Sala A"]
        self.assertEqual(rc.fmt(a["vals"][0]), "69.4%")
        self.assertEqual(a["malos"], [0])

    def test_subfilas_solo_si_los_sensores_difieren(self):
        self.assertEqual(len(self.salas["Sala A"]["sensores"]), 3)  # solo CO2 falló
        self.assertEqual(self.salas["Sala B"]["sensores"], [])     # fallaron igual
        self.assertEqual(self.salas["Sala C"]["sensores"], [])     # sin días en rojo

    def test_peores_primero(self):
        self.assertEqual([f["nombre"] for f in self.mod["fallas"]], ["Sala B", "Sala A"])


class TestBateria(unittest.TestCase):
    INV = [InvAmb("Sanna", "San Borja", "Sala A", 1, "CO2", 10),
           InvAmb("Sanna", "San Borja", "Sala A", 2, "HUMIDITY", 10),
           InvAmb("Sanna", "San Borja", "Sala B", 3, "CO2", 20),
           InvAmb("Sanna", "San Borja", "Sala C", 4, "CO2", 30)]

    def test_ultimo_valor_por_sala_y_el_mas_bajo_si_hay_varios(self):
        pedidos = []

        def consultar(base, sql, **params):
            pedidos.append((base, params))
            return [(10, Decimal("35")), (10, Decimal("12")), (20, Decimal("77"))]
        cnt = {1: {DIAS[0]: 288}, 2: {DIAS[0]: 288}}   # Sala B solo en previas, C nunca
        with mock.patch.object(rc, "consultar", consultar):
            self.assertEqual(rc.cargar_baterias(self.INV, cnt, {3}, DIAS), {10: 12.0, 20: 77.0})
        base, p = pedidos[0]
        self.assertEqual((base, p["salas"]), ("ambiental", [10, 20]))
        # Hasta el cierre del domingo (hora Lima), buscando 4 semanas antes del lunes
        self.assertEqual(p["ini"].isoformat(), "2026-08-31T00:00:00-05:00")
        self.assertEqual(p["fin"].isoformat(), "2026-10-05T00:00:00-05:00")

    def test_columna_solo_en_la_fila_de_la_sala(self):
        lect = {u: [288] * 7 for u in (1, 2, 3, 4)}
        lect[1][0] = 100                       # Sala A: solo el CO2 falla -> subfilas
        cnt = _cnt(lect)
        mod = rc.modulo_ambiental(self.INV, cnt, set(), DIAS, {10: 9.0, 20: 77.0})
        rc.calcular(mod, 90.0)
        doc = rc.armar_html([{"empresa": "Sanna", "modulos": [mod],
                              "final": rc.conectividad_final([mod])}], DIAS, 90.0)
        self.assertIn('<th class="bat" scope="col">Batería</th>', doc)
        self.assertIn('<td class="pct bajo bat">9%</td>', doc)     # < 20% en rojo
        self.assertIn('<td class="pct bat">77%</td>', doc)
        self.assertIn('<td class="pct bat">—</td>', doc)           # Sala C sin dato
        self.assertEqual(doc.count('<td class="bat"></td>'), 3)    # 2 subfilas + promedio
        # Si ningún dispositivo de la empresa reporta batería, no hay columna
        sin = rc.modulo_ambiental(self.INV, cnt, set(), DIAS, {})
        rc.calcular(sin, 90.0)
        doc = rc.armar_html([{"empresa": "Sanna", "modulos": [sin],
                              "final": rc.conectividad_final([sin])}], DIAS, 90.0)
        self.assertNotIn("Batería", doc)


class TestEnergia(unittest.TestCase):
    def test_agrupa_por_tablero_con_principal_primero(self):
        inv = [InvEne("Sanna", "San Borja", "Tablero TGA (N)", 1, "RX-PB", False),
               InvEne("Sanna", "San Borja", "Tablero TGA (N)", 2, "Llave general TGA", True),
               InvEne("Sanna", "San Borja", "Tablero TG-P1", 3, "Llave general", True),
               InvEne("Sanna", "San Borja", "Tablero TGA (N)", 4, "Otros", False)]  # sin datos
        lect = {u: [1400] * 7 for u in (1, 2, 3)}
        lect[1][6] = 0                         # RX-PB sin datos el domingo
        lect[3][0] = 0                         # y Llave general sin fila el lunes
        mod = rc.modulo_energia(inv, _cnt(lect), set(), DIAS)
        rc.calcular(mod, 90.0)
        self.assertEqual([b["titulo"] for b in mod["bloques"]],
                         ["Tablero TG-P1", "Tablero TGA (N)"])
        self.assertEqual([f["nombre"] for f in mod["bloques"][1]["filas"]],
                         ["Llave general TGA", "RX-PB"])
        self.assertEqual(mod["excluidos"], 1)
        self.assertEqual(mod["bloques"][1]["filas"][1]["malos"], [6])
        self.assertEqual(mod["bloques"][0]["filas"][0]["malos"], [0])

    def test_equipo_caido_antes_de_la_semana_sale_en_rojo(self):
        inv = [InvEne("Sanna", "San Borja", "TG", 1, "Medidor", True)]
        mod = rc.modulo_energia(inv, {}, {1}, DIAS)
        rc.calcular(mod, 90.0)
        self.assertEqual(mod["fallas"][0]["malos"], list(range(7)))


class TestConectividadFinal(unittest.TestCase):
    def test_promedio_de_energia_y_ambiental(self):
        f = rc.conectividad_final([{"prom_semana": 96.228, "titulo": "Energía"},
                                   {"prom_semana": 99.514, "titulo": "Ambiental"}])
        self.assertEqual(f, {"valor": 97.9, "nota": "(96.2% + 99.5%) ÷ 2"})

    def test_usa_cifras_impresas_y_redondea_5_hacia_arriba(self):
        # 92.85 con floats se imprimiría 92.8; a mano (90.0 + 95.7) ÷ 2 = 92.85 → 92.9
        f = rc.conectividad_final([{"prom_semana": 90.0, "titulo": "Energía"},
                                   {"prom_semana": 95.7, "titulo": "Ambiental"}])
        self.assertEqual(f["valor"], 92.9)

    def test_un_solo_modulo(self):
        f = rc.conectividad_final([{"prom_semana": 95.04, "titulo": "Energía"}])
        self.assertEqual(f, {"valor": 95.0, "nota": "solo energía"})


class TestEmpresas(unittest.TestCase):
    def test_previos_solo_para_los_que_no_tienen_datos_en_la_semana(self):
        log: list = []
        _datos(log)
        self.assertEqual([x for x in log if x[1] == "previos"],
                         [("energia", "previos", [3])])   # ambiental: todos con datos
        # "¿recién instalado?" solo se pregunta por los que SÍ tienen datos
        self.assertEqual([x for x in log if x[1] == "nuevos"],
                         [("energia", "nuevos", [1, 2, 4]), ("ambiental", "nuevos", [10, 11])])

    def test_todas_las_empresas_con_datos_sin_las_de_prueba(self):
        reportes, omitidas = rc.armar_empresas(_datos(), DIAS, 90.0)
        self.assertEqual([r["empresa"] for r in reportes], ["Oechsle", "Sanna"])
        self.assertEqual(omitidas, ["Burger King"])
        sanna = reportes[1]
        self.assertEqual([m["clave"] for m in sanna["modulos"]], ["energia", "ambiental"])
        # 1400/1440 = 97.2% y 287/288 = 99.7% -> (97.2 + 99.7) ÷ 2 = 98.45 -> 98.5%
        self.assertEqual(sanna["final"], {"valor": 98.5, "nota": "(97.2% + 99.7%) ÷ 2"})
        self.assertEqual(reportes[0]["final"], {"valor": 97.2, "nota": "solo energía"})

    def test_empresas_pedidas_en_su_orden(self):
        reportes, omitidas = rc.armar_empresas(
            _datos(), DIAS, 90.0, ["SANNA", "testcorp_email", "Nadie"])
        self.assertEqual([r["empresa"] for r in reportes], ["Sanna", "TestCorp_Email"])
        self.assertEqual(omitidas, ["Nadie (no existe en las bases)"])


class TestHTML(unittest.TestCase):
    def test_una_pagina_por_empresa_con_conectividad_final(self):
        reportes, _ = rc.armar_empresas(_datos(), DIAS, 90.0)
        doc = rc.armar_html(reportes, DIAS, 90.0)
        self.assertEqual(doc.count('<section class="empresa">'), 2)
        self.assertEqual(doc.count("Conectividad promedio de la semana"), 2)
        self.assertIn("(97.2% + 99.7%) ÷ 2", doc)
        self.assertIn("Sin monitoreo ambiental", doc)          # Oechsle
        self.assertIn("Reporte semanal de conectividad — Oechsle · Salaverry", doc)
        # Textos que se pidió quitar
        for quitado in ("Generado", "Cada celda", "Días por debajo del", "No se incluye",
                        "Todas las salas", "Todos los equipos", "En rojo"):
            self.assertNotIn(quitado, doc)

    def test_rojo_solo_bajo_umbral(self):
        mod = _ambiental()
        rep = {"empresa": "Sanna", "modulos": [mod], "final": rc.conectividad_final([mod])}
        doc = rc.armar_html([rep], DIAS, 90.0)
        # Sala A lunes + su subfila CO2 + Sala B martes + el "Promedio del día"
        # de lunes (89.8%) y martes (78.2%); la final (95.4%) no va en rojo
        self.assertEqual(doc.count('class="pct bajo"'), 5)
        self.assertIn("↳ CO₂", doc)
        self.assertIn("Sin monitoreo de energía", doc)
        self.assertNotIn("100.7%", doc)


class TestAgua(unittest.TestCase):
    def test_cada_medidor_contra_su_ritmo_y_agrupado_por_tuberia(self):
        inv = [InvAgua("Scotiabank", "Jockey Plaza", "Kitchenette", 2, "Medidor general", True),
               InvAgua("Scotiabank", "Jockey Plaza", "Cisterna", 5, "Medidor cisterna", False),
               InvAgua("Scotiabank", "Jockey Plaza", "Cisterna", 6, "Sin ritmo", False)]
        lect = {2: [144] * 7, 5: [48] * 7, 6: [144] * 7}
        lect[5][3] = 24                        # cada 30 min: 24 de 48 el jueves
        mod = rc.modulo_agua(inv, _cnt(lect), set(), DIAS, ritmos={2: 144, 5: 48})
        rc.calcular(mod, 90.0)
        filas = {f["nombre"]: f for b in mod["bloques"] for f in b["filas"]}
        self.assertEqual([b["titulo"] for b in mod["bloques"]], ["Cisterna", "Kitchenette"])
        self.assertEqual(filas["Medidor general"]["vals"], [100.0] * 7)
        self.assertEqual(rc.fmt(filas["Medidor cisterna"]["vals"][3]), "50.0%")
        self.assertEqual(filas["Medidor cisterna"]["malos"], [3])
        # Sin ritmo medible: se usa el valor por defecto (cada 10 min)
        self.assertEqual(filas["Sin ritmo"]["esperadas"], rc.ESPERADAS["agua"])
        self.assertEqual(filas["Sin ritmo"]["malos"], [])
        self.assertEqual(mod["subtitulo"], "3 medidores en 2 tuberías")
        self.assertEqual(mod["col_nombre"], "Medidor (agrupado por tubería)")

    def test_ritmo_propio_desde_la_moda_del_intervalo(self):
        pedidos = []

        def consultar(base, sql, **params):
            pedidos.append((base, sql, params))
            return [(2, Decimal("10")), (3, Decimal("30"))]
        inv = [InvAgua("Scotiabank", "Jockey Plaza", "Kitchenette", 2, "Medidor general", True),
               InvAgua("Scotiabank", "SITE Barranco", "Principal", 3, "Medidor general", True),
               InvAgua("Zeia Demo", "Surco", "Tubo", 1, "puntodemo", True)]   # nunca reportó
        with mock.patch.object(rc, "consultar", consultar):
            ritmos = rc.cargar_ritmos_agua(inv, {2: {DIAS[0]: 144}}, {3}, DIAS)
        self.assertEqual(ritmos, {2: 144, 3: 48})          # cada 10 min y cada 30 min
        base, sql, p = pedidos[0]
        self.assertEqual((base, sql, p["ids"]), ("energia", rc.SQL_RITMO_AGUA, [2, 3]))
        # La semana del reporte + las 4 previas (hora Lima)
        self.assertEqual(p["ini"].isoformat(), "2026-08-31T00:00:00-05:00")
        self.assertEqual(p["fin"].isoformat(), "2026-10-05T00:00:00-05:00")

    def test_energia_y_agua_en_tablas_separadas_y_final_de_ambas(self):
        reportes, _ = rc.armar_empresas(_scotiabank(nuevos={"agua": [3]}), DIAS, 90.0,
                                        ritmos={2: 144, 3: 48})
        self.assertEqual([r["empresa"] for r in reportes], ["Scotiabank"])   # Zeia Demo fuera
        rep = reportes[0]
        self.assertEqual([m["clave"] for m in rep["modulos"]], ["energia", "agua"])
        self.assertEqual(rep["final"], {"valor": 100.0, "nota": "(100.0% + 100.0%) ÷ 2"})
        doc = rc.armar_html(reportes, DIAS, 90.0)
        self.assertIn("<h2>Energía <span", doc)
        self.assertIn("<h2>Agua <span", doc)
        self.assertIn('<th scope="col">Agua</th>', doc)               # tabla final
        self.assertIn("Sin monitoreo ambiental", doc)
        self.assertEqual(doc.count('<section class="modulo">'), 2)


class TestRecienInstalados(unittest.TestCase):
    SITIO = [1, 0, 16, 48, 48, 48, 48]   # 1ª lectura el lunes 17:12, en línea desde el miércoles

    def test_entran_desde_la_semana_siguiente(self):
        datos = _scotiabank(nuevos={"agua": [3]}, sitio_lect=self.SITIO)
        reportes, _ = rc.armar_empresas(datos, DIAS, 90.0, ritmos={2: 144, 3: 48})
        agua = next(m for m in reportes[0]["modulos"] if m["clave"] == "agua")
        self.assertEqual(agua["sedes"], ["Jockey Plaza"])
        self.assertEqual(agua["n_filas"], 1)
        self.assertEqual(agua["nuevos"], ["SITE Barranco · Principal · Medidor general"])
        self.assertEqual(agua["fallas"], [])

    def test_sin_esa_regla_sus_dias_previos_saldrian_en_rojo(self):
        datos = _scotiabank(sitio_lect=self.SITIO)        # nadie marcado como nuevo
        reportes, _ = rc.armar_empresas(datos, DIAS, 90.0, ritmos={2: 144, 3: 48})
        agua = next(m for m in reportes[0]["modulos"] if m["clave"] == "agua")
        self.assertEqual(agua["fallas"][0]["malos"], [0, 1, 2])

    def test_aplica_a_todos_los_modulos(self):
        inv = [InvAmb("Sanna", "San Borja", "Sala A", 1, "CO2"),
               InvAmb("Sanna", "San Borja", "Sala A", 2, "HUMIDITY")]
        mod = rc.modulo_ambiental(inv, _cnt({1: [288] * 7, 2: [0, 0, 0, 288, 288, 288, 288]}),
                                  set(), DIAS, nuevos={2})
        rc.calcular(mod, 90.0)
        self.assertEqual(mod["nuevos"], ["San Borja · Sala A · HUMIDITY"])
        self.assertEqual(mod["fallas"], [])


class TestVariasSedes(unittest.TestCase):
    def test_cada_grupo_lleva_su_sede_si_la_pagina_tiene_varias(self):
        reportes, _ = rc.armar_empresas(_scotiabank(), DIAS, 90.0, ritmos={2: 144, 3: 48})
        doc = rc.armar_html(reportes, DIAS, 90.0)
        self.assertIn("Reporte semanal de conectividad — Scotiabank</h1>", doc)
        for grupo in ("Jockey Plaza · Tablero General TG", "Jockey Plaza · Kitchenette",
                      "SITE Barranco · Principal"):
            self.assertIn(f">{grupo}</td>", doc)

    def test_con_una_sede_va_en_el_titulo_y_no_en_cada_grupo(self):
        reportes, _ = rc.armar_empresas(_scotiabank(nuevos={"agua": [3]}), DIAS, 90.0,
                                        ritmos={2: 144, 3: 48})
        doc = rc.armar_html(reportes, DIAS, 90.0)
        self.assertIn("Reporte semanal de conectividad — Scotiabank · Jockey Plaza</h1>", doc)
        self.assertIn(">Kitchenette</td>", doc)
        self.assertIn(">Tablero General TG</td>", doc)
        self.assertNotIn("Jockey Plaza · ", doc)


if __name__ == "__main__":
    unittest.main()
