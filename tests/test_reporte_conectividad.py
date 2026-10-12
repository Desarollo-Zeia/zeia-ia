"""Pruebas del reporte semanal de conectividad (sin base de datos).

    python -m unittest tests.test_reporte_conectividad -v
"""
from __future__ import annotations

import importlib.util
import unittest
from collections import namedtuple
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

RUTA = Path(__file__).resolve().parent.parent / "scripts" / "reporte_conectividad.py"
_spec = importlib.util.spec_from_file_location("reporte_conectividad", RUTA)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

DIAS = [date(2026, 9, 28) + timedelta(days=i) for i in range(7)]
InvAmb = namedtuple("InvAmb", "empresa sede sala uid indicador")
InvEne = namedtuple("InvEne", "empresa sede tablero uid nombre principal")


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


def _falso_consultar(inv: dict, lect: dict, previos: dict | None = None, log: list | None = None):
    """Sustituye a rc.consultar: responde según la base y la consulta pedida."""
    previos = previos or {}

    def consultar(base, sql, **params):
        sql_inv, sql_dias, sql_prev = rc.SQL[base]
        if sql == sql_inv:
            return inv[base]
        if sql == sql_dias:
            return [(uid, DIAS[i], n) for uid, v in lect[base].items()
                    if uid in params["ids"] for i, n in enumerate(v) if n]
        if sql == sql_prev:
            if log is not None:
                log.append((base, sorted(params["ids"])))
            return [(u,) for u in previos.get(base, ()) if u in params["ids"]]
        raise AssertionError(f"consulta inesperada: {sql[:40]}")
    return consultar


def _datos(log: list | None = None) -> dict:
    """Dos bases con empresas reales, una sin datos y dos de prueba."""
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
        return {base: rc.cargar(base, DIAS) for base in rc.BASES}


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
        self.assertEqual(log, [("energia", [3])])   # ambiental: todos con datos

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


if __name__ == "__main__":
    unittest.main()
