"""Pruebas de los avisos en vivo (src/avisos_vivo.py): estado, comandos, motor y backtest.

Sin red ni base: el estado va en SQLite en memoria (y uno en una carpeta
temporal que la prueba limpia sola) y los datos salen de la base falsa de
test_avisos_datos.

    python -m unittest tests.test_avisos_vivo -v
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from src import avisos as av
from src import avisos_vivo as vivo
from src.avisos_datos import Historia

try:
    from tests.test_avisos_datos import FakeDB, T, cada, inv_energia
except ImportError:                     # python -m unittest discover -s tests
    from test_avisos_datos import FakeDB, T, cada, inv_energia

RUTA = Path(__file__).resolve().parent.parent / "scripts" / "avisos.py"
_spec = importlib.util.spec_from_file_location("avisos_script", RUTA)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def db_dos_puntos(desde=T(9, 25), hasta=T(10, 4)) -> FakeDB:
    """2 puntos del Gateway 1 de Sanna que se cortan juntos el 02-oct de 10:00 a 11:30."""
    db = FakeDB()
    db.inv_e = [inv_energia(1, "Llave general"),
                inv_energia(2, "Tomógrafo", tablero="TG-RT", eui="00956906000506C8")]
    lect = [t for t in cada(desde, hasta) if not T(10, 2, 10) <= t < T(10, 2, 11, 30)]
    for uid in (1, 2):
        db.energia[uid] = [(t, 1000.0 + i) for i, t in enumerate(lect)]
    return db


class Reloj:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class StoreTest(unittest.TestCase):
    def test_estado_mensajes_y_mantenimientos(self):
        s = vivo.Store(":memory:")
        e = av.Estado(ultimo_resumen="2026-10-21")
        s.guardar_estado(e)
        self.assertEqual(s.estado().ultimo_resumen, "2026-10-21")
        s.registrar(av.Mensaje("🟠 uno\ndetalle", av.NARANJA, False, T(10, 21, 9)), "simular", False)
        self.assertEqual(s.ultimos(5)[0]["texto"], "🟠 uno\ndetalle")
        m = s.poner_mantenimiento("Sanna", T(10, 21, 9), T(10, 21, 11), por="Ana")
        self.assertEqual([x.alcance for x in s.mantenimientos(T(10, 21, 10))], ["Sanna"])
        self.assertEqual(s.mantenimientos(T(10, 21, 11, 30)), [])
        self.assertEqual(len(s.mantenimientos(T(10, 21), T(10, 21, 23))), 1)   # se cruza con el día
        self.assertEqual([x.id for x in s.terminar_mantenimientos(T(10, 21, 10), "sanna")], [m.id])
        self.assertEqual(s.mantenimientos(T(10, 21, 10, 5)), [])

    def test_en_archivo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            ruta = Path(d) / "sub" / "avisos.sqlite"
            s = vivo.Store(ruta)
            s.escribir("k", "v")
            self.assertEqual(vivo.Store(ruta).leer("k"), "v")      # otra conexión lo ve


class Comandos(unittest.TestCase):
    def setUp(self):
        self.s = vivo.Store(":memory:")
        unidades = [av.Unidad("energia:3", "energia", "Llave general cuarto de bombas", "TFC-BAF",
                              "Sanna", "San Borja", "Gateway 2 - PB Exterior"),
                    av.Unidad("ambiental:147", "ambiental", "Subestación eléctrica", "", "Sanna",
                              "San Borja", "Gateway 2 - PB Exterior", 5),
                    av.Unidad("energia:76", "energia", "TG-TR2 (TF-AA)", "TG-TR2", "Oechsle",
                              "Salaverry")]
        self.s.escribir("unidades", json.dumps([vivo._u_dict(u) for u in unidades]))
        self.t = T(10, 21, 10)

    def cm(self, args):
        return vivo.comando_mantenimiento(args, por="Ana", store=self.s, modo="simular", t=self.t)

    def test_duraciones(self):
        self.assertEqual([vivo.duracion_min(x) for x in ("2h", "90m", "1d", "2h30m", "45", "45min")],
                         [120, 90, 1440, 150, 45, 45])
        self.assertIsNone(vivo.duracion_min("Sanna"))
        self.assertIsNone(vivo.duracion_min("2"[:0]))

    def test_poner_listar_y_terminar(self):
        r = self.cm("Gateway 2 3h")
        self.assertIn("🛠 Mantenimiento hasta las 13:00 (3 h) para «Gateway 2»: 2", r)
        self.assertIn("Llave general cuarto de bombas, Subestación eléctrica", r)
        self.assertIn("• Gateway 2 hasta las 13:00 (Ana)", self.cm(""))
        self.assertIn("Terminé el mantenimiento de «Gateway 2»", self.cm("fin"))
        self.assertIn("No hay mantenimientos en curso", self.cm(""))

    def test_sin_duracion_son_2_horas_y_todo(self):
        self.assertIn("hasta las 12:00 (2 h) para «oechsle»: 1 (TG-TR2 (TF-AA))", self.cm("oechsle"))
        self.assertIn("para «todo»: todo", self.cm("todo 30m"))

    def test_lo_que_no_existe_no_se_crea(self):
        self.assertIn("No encontré nada con «Zona Naranja»", self.cm("Zona Naranja 1h"))
        self.assertEqual(self.s.mantenimientos(self.t), [])
        self.assertIn("de 1 minuto a 7 días", self.cm("Sanna 30d"))

    def test_apagado(self):
        self.assertIn("apagados", vivo.comando_mantenimiento("Sanna", modo="apagado"))
        self.assertIn("apagados", vivo.texto_estado(modo="apagado"))


class MotorTest(unittest.TestCase):
    def motor(self, modo, t, db=None, enviar=None):
        db = db or db_dos_puntos()
        return vivo.Motor(modo=modo, store=vivo.Store(":memory:"), reglas=av.Reglas(),
                          crear_historia=lambda _t: Historia(T(10, 1), db), enviar=enviar,
                          ahora=Reloj(t), log=lambda m: None, ciclo_min=5, pausa_s=0)

    def test_simular_no_envia_y_queda_en_avisos(self):
        enviar = mock.Mock()
        m = self.motor("simular", T(10, 2, 10, 25), enviar=enviar)
        msgs = m.ciclo()
        enviar.assert_not_called()
        self.assertEqual([x.tipo for x in msgs], ["aviso", "resumen"])   # el 1er ciclo resume ayer
        self.assertIn("🔴 Gateway 1 - PB Interior caído (Sanna San Borja)", msgs[0].texto)
        txt = vivo.texto_estado(store=m.store, modo="simular", t=T(10, 2, 10, 26))
        self.assertIn("SIMULACIÓN", txt)
        self.assertIn("Último ciclo: las 10:25 · vigilando 2 · cada 5 min", txt)
        self.assertIn("Abiertos (1):", txt)
        self.assertIn("Últimos avisos simulados:", txt)

    def test_enviar_y_reintentar_lo_que_falla(self):
        enviados = []
        caida = {"si": True}

        def enviar(texto, silencioso):
            if caida["si"]:
                raise RuntimeError("sin internet")
            enviados.append((texto.splitlines()[0], silencioso))

        reloj = Reloj(T(10, 2, 10, 25))
        m = self.motor("enviar", reloj.t, enviar=enviar)
        m.ahora = reloj
        m.ciclo()
        self.assertEqual(len(json.loads(m.store.leer("pendientes"))), 2)
        caida["si"] = False
        reloj.t = T(10, 2, 10, 30)
        m.ciclo()
        self.assertEqual([s for _, s in enviados], [False, True])   # 🔴 con sonido, 🟡 sin
        self.assertEqual(json.loads(m.store.leer("pendientes")), [])
        self.assertTrue(all(f["enviado"] for f in m.store.ultimos(5)))

    def test_base_caida_avisa_al_tercer_ciclo(self):
        db = db_dos_puntos()
        db.falla.add("inv_energia")
        reloj = Reloj(T(10, 2, 10))
        m = self.motor("simular", reloj.t, db=db)
        m.ahora = reloj
        textos = []
        for i in range(3):
            reloj.t = T(10, 2, 10, 5 * i)
            textos += [x.texto for x in m.ciclo()]
        self.assertTrue(any("⚙️ El monitor de avisos no puede leer la base de energía" in x
                            for x in textos), textos)
        ciclo = json.loads(m.store.leer("ciclo"))
        self.assertIn("túnel caído", ciclo["errores"]["energia"])


class Backtest(unittest.TestCase):
    def test_de_punta_a_punta(self):
        db = db_dos_puntos()
        res = vivo.backtest(date(2026, 10, 2), date(2026, 10, 3), av.Reglas(), 5, consultar=db)
        textos = [m.texto for m in res["mensajes"]]
        rojos = [m for m in res["mensajes"] if m.nivel == av.ROJO and not m.silencioso]
        self.assertEqual(len(rojos), 1)
        self.assertEqual(av.hhmm(rojos[0].t), "10:20")
        self.assertIn("Gateway 1 - PB Interior caído (Sanna San Borja)", rojos[0].texto)
        self.assertEqual(sum(m.tipo == "resumen" for m in res["mensajes"]), 3)
        resumen_2 = [x for x in textos if "Resumen ZEIA de ayer, viernes 2 oct" in x][0]
        self.assertIn("93.8% de lecturas · sin datos 10:00–11:30 (1 h 30 min)", resumen_2)
        corto, largo = script.informe(res, date(2026, 10, 2), date(2026, 10, 3))
        self.assertIn("Backtest de avisos 2026-10-02 → 2026-10-03 (2 días)", corto)
        self.assertIn("con sonido 1 (🔴 1)", corto)
        self.assertIn("[02-oct 10:20] 🔊 🔴 Gateway 1 - PB Interior caído", largo)


if __name__ == "__main__":
    unittest.main()
