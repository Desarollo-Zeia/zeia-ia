"""Pruebas de la historia en memoria de los avisos (src/avisos_datos.py), sin base.

Una base falsa (FakeDB) responde cada consulta de avisos_datos.SQL a partir
de lecturas sintéticas, respetando las ventanas ini/fin como Postgres.

    python -m unittest tests.test_avisos_datos -v
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from src import avisos as av
from src.avisos_datos import Historia

LIMA = av.LIMA
UTC = timezone.utc


class F(SimpleNamespace):
    """Fila como la de SQLAlchemy 2: `.t` es la fila misma, no una columna "t"."""

    @property
    def t(self):
        return self


def T(mes: int, dia: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(2026, mes, dia, hh, mm, tzinfo=LIMA)


def cada(ini: datetime, fin: datetime, minutos: int = 1) -> list:
    out, t = [], ini
    while t < fin:
        out.append(t)
        t += timedelta(minutes=minutos)
    return out


def hora(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


class FakeDB:
    """Imita las consultas de avisos_datos.SQL con datos en memoria."""

    def __init__(self):
        self.inv_e, self.inv_a, self.inv_w = [], [], []
        self.energia: dict = {}      # uid → [(t, ep)]
        self.ambiental: dict = {}    # id de indicator_device → [(t, valor)]
        self.agua: dict = {}         # uid → [(t, litros, bateria, medicion, tuberia, snr)]
        self.bat, self.snr, self.bat_ahora = [], [], []
        self.llamadas: list = []
        self.falla: set = set()      # nombres de consulta que fallan

    def __call__(self, nombre, base, sql, p):
        self.llamadas.append((nombre, dict(p)))
        if nombre in self.falla:
            raise RuntimeError(f"[{base}] túnel caído")
        return getattr(self, "q_" + nombre)(p)

    @staticmethod
    def _en(t, p, a="ini", b="fin"):
        return p[a] <= t < p[b]

    def q_inv_energia(self, p):
        return self.inv_e

    def q_inv_ambiental(self, p):
        return self.inv_a

    def q_inv_agua(self, p):
        return self.inv_w

    def q_pres_energia(self, p):
        masks: dict = {}
        for uid, lst in self.energia.items():
            if uid in p["ids"]:
                for t, _ in lst:
                    if self._en(t, p):
                        masks[(uid, hora(t))] = masks.get((uid, hora(t)), 0) | (1 << t.minute)
        return [F(uid=u, h=h, mask=m) for (u, h), m in masks.items()]

    def q_cont_energia(self, p):
        out = []
        for uid, lst in self.energia.items():
            if uid not in p["ids"]:
                continue
            horas: dict = {}
            prev = None
            for t, ep in lst:
                if not self._en(t, p) or ep <= 0:
                    continue
                paso = tasa = None
                if prev is not None:
                    paso = ep - prev[1]
                    tasa = paso / max((t - prev[0]).total_seconds() / 60, 1)
                horas.setdefault(hora(t), []).append((ep, paso, tasa))
                prev = (t, ep)
            for h, xs in horas.items():
                pasos = [x[1] for x in xs if x[1] is not None]
                tasas = [x[2] for x in xs if x[2] is not None]
                out.append(F(uid=uid, h=h, ep_min=min(x[0] for x in xs),
                             ep_max=max(x[0] for x in xs),
                             tasa_max=max(tasas) if tasas else None,
                             paso_min=min(pasos) if pasos else None,
                             paso_max=max(pasos) if pasos else None))
        return out

    def q_lect_ambiental(self, p):
        return [F(uid=uid, ts=t, v=v) for uid in sorted(self.ambiental) if uid in p["ids"]
                for t, v in self.ambiental[uid] if self._en(t, p)]

    def q_lect_agua(self, p):
        return [F(uid=uid, ts=x[0], litros=x[1], estado_bateria=x[2], estado_medicion=x[3],
                  estado_tuberia=x[4], snr=x[5])
                for uid in sorted(self.agua) if uid in p["ids"]
                for x in self.agua[uid] if self._en(x[0], p)]

    def q_bat_ambiental(self, p):
        return [F(sala_id=s, eui=e, ts=t, bateria=b) for s, e, t, b in self.bat
                if s in p["salas"] and self._en(t, p)]

    def q_bat_ambiental_ahora(self, p):
        return [F(device_id=d, bateria=b) for d, b in self.bat_ahora if d in p["ids"]]

    def q_snr_ambiental(self, p):
        a, b = p["ini"].astimezone(LIMA).date(), p["fin"].astimezone(LIMA).date()
        return [F(sala_id=s, eui=e, dia=d, snr=v) for s, e, d, v in self.snr
                if s in p["salas"] and a <= d <= b]

    def _ult(self, fuente, p, extraer):
        out = []
        for uid in p["ids"]:
            ts = [extraer(x) for x in fuente.get(uid, []) if self._en(extraer(x), p, "desde", "hasta")]
            if ts:
                out.append(F(uid=uid, ult=max(ts)))
        return out

    def q_ult_energia(self, p):
        return self._ult(self.energia, p, lambda x: x[0])

    def q_ult_ambiental(self, p):
        return self._ult(self.ambiental, p, lambda x: x[0])

    def q_ult_agua(self, p):
        return self._ult(self.agua, p, lambda x: x[0])

    def q_alguna_energia(self, p):
        return [F(uid=u) for u in p["ids"] if self.energia.get(u)]

    def q_alguna_ambiental(self, p):
        return [F(uid=u) for u in p["ids"] if self.ambiental.get(u)]

    def q_alguna_agua(self, p):
        return [F(uid=u) for u in p["ids"] if self.agua.get(u)]


def inv_energia(uid, nombre, empresa="Sanna", sede="San Borja", tablero="TG-P1",
                eui="009569060004F73E", alta=None):
    return F(empresa=empresa, sede=sede, tablero=tablero, eui=eui, uid=uid, nombre=nombre,
             alta=alta or T(1, 1))


class Energia(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.db.inv_e = [inv_energia(1, "Llave general")]
        lect = [t for t in cada(T(10, 1), T(10, 4)) if not T(10, 2, 10) <= t < T(10, 2, 11, 30)]
        self.db.energia[1] = [(t, 1000.0 + i) for i, t in enumerate(lect)]
        self.h = Historia(T(10, 1), self.db)
        self.assertEqual(self.h.actualizar(T(10, 4)), {"energia": None, "ambiental": None})

    def test_ultima_estable_y_regreso(self):
        h, k = self.h, "energia:1"
        self.assertEqual(h.ultima(k, T(10, 2, 11)), T(10, 2, 9, 59))
        self.assertFalse(h.estable(k, T(10, 2, 11, 40), 30))
        self.assertTrue(h.estable(k, T(10, 2, 12, 5), 30))
        self.assertEqual(h.regreso(k, T(10, 2, 12, 5), T(10, 2, 9, 59)), T(10, 2, 11, 30))
        self.assertEqual(h.primera_desde(k, T(10, 2, 9, 59), T(10, 2, 12)), T(10, 2, 11, 30))

    def test_observar_con_gateway_y_sin_mirar_el_futuro(self):
        obs = self.h.observar(T(10, 2, 11))
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual(o.unidad.gateway, "Gateway 1 - PB Interior")
        self.assertEqual(o.ultima, T(10, 2, 9, 59))
        self.assertIsNone(o.plano_h)        # sin datos frescos no se mira el contador

    def test_dia_con_un_corte(self):
        d = {x.unidad.clave: x for x in self.h.dia(date(2026, 10, 2))}["energia:1"]
        self.assertAlmostEqual(d.cobertura, 100 * (1440 - 90) / 1440)
        self.assertEqual(d.cortes, [(T(10, 2, 10), 90)])
        self.assertEqual(d.raros, [])        # menos de 3 días de referencia: no juzga saltos

    def test_cadencia_energia(self):
        self.h.calcular_cadencias(T(10, 4))
        self.assertEqual(self.h.unidades["energia:1"].cadencia, 1)


class Contador(unittest.TestCase):
    """Sube de 08:00 a 20:00 todos los días y desde el 02-oct 20:00 se queda quieto."""

    def setUp(self):
        db = FakeDB()
        db.inv_e = [inv_energia(1, "Llave general")]
        ep, lect = 5000.0, []
        for t in cada(T(9, 24), T(10, 4)):
            tl = t.astimezone(LIMA)
            if 8 <= tl.hour < 20 and t < T(10, 2, 20):
                ep += 1.0
            lect.append((t, ep))
        lect.append((T(10, 3, 12, 0, ), 0.0))   # falla conocida de los ADW210: se ignora
        lect.sort()
        db.energia[1] = lect
        self.h = Historia(T(9, 24), db)
        self.h.actualizar(T(10, 4))

    def test_quietud_frente_a_su_semana(self):
        plano, ref, cero = self.h.plano("energia:1", T(10, 3, 22))
        self.assertEqual((plano, ref, cero), (26.0, 12.0, False))   # horas CON lecturas
        o = [x for x in self.h.observar(T(10, 3, 22))][0]
        self.assertAlmostEqual(o.plano_h, 26.0)
        motivo = av._no_mide(o, av.Reglas())
        self.assertIn("el contador no avanza hace 1 día 2 h aunque llegan lecturas "
                      "(en su semana estuvo hasta 12 h sin avanzar)", motivo[0])

    def test_poca_historia_no_juzga(self):
        plano, ref, _ = self.h.plano("energia:1", T(9, 25, 22))
        self.assertIsNotNone(plano)
        self.assertIsNone(ref)

    def test_horas_sin_lecturas_no_cuentan_como_quietud(self):
        db = FakeDB()
        db.inv_e = [inv_energia(1, "Llave general")]
        ep, lect = 5000.0, []
        for t in cada(T(9, 24), T(10, 4)):
            if T(10, 2, 20) <= t < T(10, 3, 18):
                continue                         # desconectado: 22 h sin lecturas
            if t.astimezone(LIMA).hour % 2 == 0 and t < T(10, 2, 20):
                ep += 1.0
            lect.append((t, ep))
        db.energia[1] = lect
        h = Historia(T(9, 24), db)
        h.actualizar(T(10, 4))
        plano, ref, _ = h.plano("energia:1", T(10, 3, 22))
        self.assertEqual(plano, 5.0)             # 19:00 del 02-oct + 18:00–21:59 del 03, no 27 h
        self.assertEqual(ref, 1.0)

    def test_contador_en_cero(self):
        db = FakeDB()
        db.inv_e = [inv_energia(38, "Aire acondicionado cocina", tablero="TGA (N)")]
        lect = []
        for i, t in enumerate(cada(T(9, 24), T(10, 4))):
            lect.append((t, 0.0 if t >= T(10, 2) else 100.0 + i))
        db.energia[38] = lect
        h = Historia(T(9, 24), db)
        h.actualizar(T(10, 4))
        plano, ref, cero = h.plano("energia:38", T(10, 3, 6))
        self.assertEqual((plano, ref, cero), (30.0, 0.0, True))
        o = h.observar(T(10, 3, 6))[0]
        self.assertIn("envía el contador en 0 hace 1 día 6 h (normalmente avanza cada hora)",
                      av._no_mide(o, av.Reglas())[0])

    def test_lectura_en_cero_no_es_salto_ni_retroceso(self):
        d = {x.unidad.clave: x for x in self.h.dia(date(2026, 10, 3))}["energia:1"]
        self.assertEqual(d.raros, [])


class Ambiental(unittest.TestCase):
    def setUp(self):
        db = FakeDB()
        db.inv_a = [F(empresa="Sanna", sede="San Borja", sala_id=364, sala="Zona Verde",
                      device_id=184, eui="24e124128c069408", modelo="AM107", uid=u, indicador=i)
                    for u, i in ((375, "CO2"), (376, "HUMIDITY"), (377, "TEMPERATURE"))]
        ts = cada(T(9, 28, 0, 1), T(10, 4), 5)
        db.ambiental[375] = [(t, str(450 + (i % 7))) for i, t in enumerate(ts)]
        db.ambiental[375][400] = (db.ambiental[375][400][0], "0")      # valor imposible
        db.ambiental[376] = [(t, str(50 + (i % 3))) for i, t in enumerate(ts)
                             if not T(10, 2, 12) <= t < T(10, 2, 16)]
        db.ambiental[377] = [(t, "21.5" if T(10, 2, 6) <= t < T(10, 2, 10) else str(20 + i % 4))
                             for i, t in enumerate(ts)]
        db.bat = [(364, "24e124128c069408", T(10, 1, 9), 12), (364, "24e124128c069408", T(10, 2, 9), 8)]
        db.snr = [(364, "24e124128c069408", date(2026, 9, 25) + timedelta(days=i), 10.0)
                  for i in range(7)] + [(364, "24e124128c069408", date(2026, 10, 2), -12.0)]
        self.db = db
        self.h = Historia(T(9, 28), db)
        self.h.actualizar(T(10, 4))
        self.k = "ambiental:184"

    def test_unidad_por_sensor_con_sus_indicadores(self):
        u = self.h.unidades[self.k]
        self.assertEqual((u.nombre, u.modulo, u.gateway), ("Zona Verde", "ambiental",
                                                           "Gateway 1 - PB Interior"))
        self.assertEqual(len(self.h.series[self.k]), 3)
        self.h.calcular_cadencias(T(10, 4))
        self.assertEqual(u.cadencia, 5)

    def test_valor_fijo(self):
        f = self.h.fijos(self.k, T(10, 2, 10))
        self.assertGreater(f["TEMPERATURE"], 3.8)
        self.assertLess(f["CO2"], 0.1)

    def test_dia_peor_indicador_bloque_y_raros(self):
        self.h.calcular_cadencias(T(10, 2))
        d = {x.unidad.clave: x for x in self.h.dia(date(2026, 10, 2))}[self.k]
        self.assertAlmostEqual(d.cobertura, 100 * 20 / 24, places=0)   # la humedad: 4 h sin datos
        self.assertEqual(len(d.cortes), 1)
        self.assertEqual(av.hhmm(d.cortes[0][0]), "12:01")   # la de 11:56 cubre hasta 12:00
        self.assertLess(d.peor_bloque, 70)
        self.assertEqual(d.bateria, 8)
        self.assertEqual((d.snr, d.snr_ref), (-12.0, 10.0))
        self.assertTrue(any("marcó 0 ppm (imposible)" in x for x in
                            [x for dd in self.h.dia(date(2026, 9, 29)) for x in dd.raros]))

    def test_bateria_en_el_tiempo_y_la_actual(self):
        self.assertEqual(self.h.bateria(self.k, T(10, 2, 8)), 12)
        self.assertEqual(self.h.bateria(self.k, T(10, 2, 10)), 8)
        self.db.bat_ahora = [(184, 100)]
        self.h.refrescar_baterias(T(10, 4))
        self.assertEqual(self.h.bateria(self.k, T(10, 4, 0, 5)), 100)

    def test_recarga_incremental_no_duplica(self):
        n = len(self.h.corridas[f"{self.k}:375"])
        nuevas = cada(T(10, 4), T(10, 4, 1), 5)
        self.db.ambiental[375] += [(t, "999") for t in nuevas]
        self.h.actualizar(T(10, 4, 1))
        self.h.actualizar(T(10, 4, 2))           # se solapa 3 min: no repite
        self.assertEqual(len(self.h.corridas[f"{self.k}:375"]), n + 1)

    def test_si_falla_una_base_la_otra_sigue(self):
        self.db.falla.add("lect_ambiental")
        err = self.h.actualizar(T(10, 4, 10))
        self.assertIsNone(err["energia"])
        self.assertIn("túnel caído", err["ambiental"])
        self.assertEqual(self.h.cargado["ambiental"], T(10, 4))   # reintenta desde ahí


class Robustez(unittest.TestCase):
    def test_referencia_percentil_75(self):
        from src.avisos_datos import referencia
        self.assertIsNone(referencia([10, 11]))
        self.assertEqual(referencia([10.0] * 5 + [-12.0] * 9), 10.0)   # 9 días malos de 14
        self.assertEqual(referencia([1, 2, 3, 4]), 3)

    def test_cadencia_nominal_no_se_corre_con_la_degradacion(self):
        db = FakeDB()
        db.inv_a = [F(empresa="Sanna", sede="San Borja", sala_id=364, sala="Zona Verde",
                      device_id=184, eui="24e124128c069408", modelo="AM107", uid=375,
                      indicador="CO2")]
        normal = cada(T(9, 28, 0, 1), T(10, 1), 5)                 # 3 días cada 5 min
        degradado = cada(T(10, 1, 0, 1), T(10, 12), 10)            # 11 días cada 10 min
        db.ambiental[375] = [(t, "450") for t in normal + degradado]
        h = Historia(T(9, 28), db)
        h.actualizar(T(10, 12))
        h.calcular_cadencias(T(10, 12))
        self.assertEqual(h.unidades["ambiental:184"].cadencia, 5)


class AguaYAltas(unittest.TestCase):
    def setUp(self):
        db = FakeDB()
        db.inv_e = [inv_energia(1, "Llave general"),
                    inv_energia(5, "Resonador", tablero="TGA (N)"),
                    inv_energia(7, "Tablero viejo"),
                    inv_energia(233, "Tablero existente de agencia", "Scotiabank", "Barranco",
                                "TG-N", "24e124621f409159", T(10, 2, 9))]
        db.inv_w = [F(empresa="Scotiabank", sede="Jockey Plaza", tuberia="Kitchenette", uid=2,
                      nombre="Medidor general", alta=T(9, 1))]
        db.energia[1] = [(t, 100.0) for t in cada(T(9, 28), T(10, 4), 10)]
        db.energia[5] = [(t, 100.0) for t in cada(T(10, 3, 10), T(10, 4))]
        db.energia[7] = [(T(9, 20, 8), 50.0)]
        litros, agua = 1000.0, []
        for t in cada(T(9, 28), T(10, 4), 10):
            if 8 <= t.astimezone(LIMA).hour < 18:
                litros += 2
            vacia = T(10, 2) <= t < T(10, 2, 8)
            agua.append((t, litros, "normal", "correcta", "vacia" if vacia else "normal", 7.0))
        db.agua[2] = agua
        self.db = db
        self.h = Historia(T(9, 28), db)
        self.h.actualizar(T(10, 4))

    def test_altas_bajas_y_previas(self):
        h = self.h
        self.assertEqual(h.previa["energia:7"], T(9, 20, 8))
        self.assertIn("energia:233", h.nunca)
        self.assertEqual(h.instalada["energia:5"], T(10, 3, 10))
        claves = {o.unidad.clave for o in h.observar(T(10, 2))}
        self.assertEqual(claves, {"energia:1", "energia:7", "agua:2"})   # 5 aún no, 233 nunca
        self.assertEqual([u.clave for u, _ in h.sin_primera(T(10, 3, 10))], ["energia:233"])
        self.assertEqual(h.sin_primera(T(10, 2, 20)), [])                # alta hace < 24 h
        dia3 = {d.unidad.clave: d for d in h.dia(date(2026, 10, 3))}
        self.assertIsNone(dia3["energia:5"].cobertura)                    # día de instalación
        self.assertNotIn("energia:5", {d.unidad.clave for d in h.dia(date(2026, 10, 2))})
        self.assertEqual(dia3["energia:7"].cobertura, 0.0)                # caído: 0 %
        alguna = [p["ids"] for n, p in self.db.llamadas if n == "alguna_energia"]
        self.assertEqual(alguna, [[233]])

    def test_agua_estado_y_cadencia(self):
        h = self.h
        estado, horas = h.estado_agua("agua:2", T(10, 2, 7))
        self.assertEqual(estado, "tubería vacia")
        self.assertAlmostEqual(horas, 6 + 50 / 60, places=1)
        self.assertIsNone(h.estado_agua("agua:2", T(10, 2, 12)))
        h.calcular_cadencias(T(10, 4))
        self.assertEqual(h.unidades["agua:2"].cadencia, 10)
        plano, ref, cero = h.plano("agua:2", T(10, 3, 23))
        self.assertEqual(plano, 5.0)             # 18:00–22:59 con lecturas, sin subir
        self.assertEqual(ref, 14.0)              # de noche: 18:00 → 08:00
        self.assertFalse(cero)

    def test_recortar_guarda_la_ultima_de_lo_que_se_va(self):
        h = self.h
        h.recortar(T(10, 4), dias=2)
        self.assertEqual(h.t0, T(10, 2).astimezone(UTC))
        self.assertEqual(h.ultima("energia:1", T(10, 3, 12)), T(10, 3, 11, 50))
        self.assertEqual(h.ultima("energia:7", T(10, 3)), T(9, 20, 8))


if __name__ == "__main__":
    unittest.main()
