"""Pruebas del motor de avisos (src/avisos.py): escenarios sintéticos, sin red ni base.

    python -m unittest tests.test_avisos -v
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from src import avisos as av

LIMA = av.LIMA
R = av.Reglas()


def T(dia: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, dia, hh, mm, tzinfo=LIMA)


def cada(ini: datetime, fin: datetime, minutos: int = 1) -> list:
    out, t = [], ini
    while t < fin:
        out.append(t)
        t += timedelta(minutes=minutos)
    return out


def U(clave: str, modulo: str = "energia", nombre: str = "", gateway=None,
      empresa: str = "Sanna", sede: str = "San Borja", cad: int = 1, grupo: str = "TG"):
    return av.Unidad(clave, modulo, nombre or clave, "" if modulo == "ambiental" else grupo,
                     empresa, sede, gateway, cad)


class Sim:
    """Corre el motor paso a paso con lecturas sintéticas (como el backtest)."""

    def __init__(self, unidades, lecturas, mants=(), reglas=R):
        self.u, self.lect, self.mants, self.r = unidades, lecturas, list(mants), reglas
        self.estado, self.msgs, self.eventos = av.Estado(), [], []

    def obs(self, t, extra=None):
        out = []
        for u in self.u:
            todas = self.lect.get(u.clave, [])
            antes = [x for x in todas if x < t]
            if not antes:
                continue
            datos = extra(u.clave, t) if extra else {}
            out.append(av.Obs(u, ultima=antes[-1], lecturas=todas, **datos))
        return out

    def correr(self, ini, fin, paso=5, extra=None, errores=None):
        t = ini
        while t < fin:
            err = errores(t) if errores else None
            evs = av.evaluar(self.estado, self.obs(t, extra), t, self.r, self.mants, err)
            self.eventos += evs
            self.msgs += [(t, m) for m in av.componer(evs, t, self.r)]
            t += timedelta(minutes=paso)
        return self.msgs


class Formato(unittest.TestCase):
    def test_duracion(self):
        self.assertEqual(av.duracion(45), "45 min")
        self.assertEqual(av.duracion(121), "2 h 1 min")
        self.assertEqual(av.duracion(180), "3 h")
        self.assertEqual(av.duracion(1500), "1 día 1 h")
        self.assertEqual(av.duracion(2880), "2 días")

    def test_cuando(self):
        ref = T(21, 10)
        self.assertEqual(av.cuando(T(21, 9, 5), ref), "las 09:05")
        self.assertEqual(av.cuando(T(20, 23, 59), ref), "ayer 23:59")
        self.assertEqual(av.cuando(T(17, 10, 15), ref), "el 17-oct 10:15")

    def test_cortes_del_dia(self):
        self.assertEqual(av.txt_cortes([(T(15, 11, 31), 749)]), "sin datos 11:31–24:00 (12 h 29 min)")
        self.assertEqual(av.txt_cortes([(T(15, 1), 10), (T(15, 5), 20)]),
                         "2 cortes: 01:00–01:10 y 05:00–05:20")
        tres = [(T(15, 1), 10), (T(15, 5), 159), (T(15, 9), 15)]
        self.assertEqual(av.txt_cortes(tres),
                         "intermitente: 3 cortes, el más largo 05:00–07:39 (2 h 39 min)")

    def test_umbrales_por_cadencia(self):
        self.assertEqual(av.hueco_corte(1), 6)           # energía: 5 min sin datos
        self.assertEqual(av.hueco_corte(5), 12)          # ambiental: 2 lecturas perdidas
        self.assertEqual(av.umbral_sin_datos(1), 60)
        self.assertEqual(av.umbral_sin_datos(30), 105)   # agua lenta: 3 envíos perdidos
        self.assertEqual(av.umbral_grupo(5), 20)

    def test_reglas_desde_config(self):
        r = av.Reglas.desde_config("07:30-19:00", "09:15")
        self.assertEqual((r.horario_ini, r.horario_fin, r.resumen_min), (450, 1140, 555))
        r = av.Reglas.desde_config("cualquier cosa", "")
        self.assertEqual((r.horario_ini, r.horario_fin, r.resumen_min), (480, 1200, 480))

    def test_norm_sin_tildes(self):
        self.assertEqual(av.norm("  Ducto  Resonador MAGNÉTICO "), "ducto resonador magnetico")


class ObsPorDefecto(unittest.TestCase):
    def test_estable_y_regreso(self):
        u = U("e1")
        lect = cada(T(21, 8), T(21, 10)) + cada(T(21, 12), T(21, 13))
        o = av.Obs(u, ultima=lect[-1], lecturas=lect)
        self.assertFalse(o.estable(T(21, 12, 5), 30))     # el hueco entra en la ventana
        self.assertTrue(o.estable(T(21, 12, 30), 30))
        self.assertFalse(o.estable(T(21, 13, 30), 30))    # se cortó a las 12:59
        self.assertEqual(o.regreso(T(21, 12, 30), T(21, 9, 59)), T(21, 12))
        self.assertEqual(o.primera_desde(T(21, 9, 59), T(21, 13)), T(21, 12))


class SinDatos(unittest.TestCase):
    def test_en_horario_avisa_y_cierra_con_duracion(self):
        a = U("e1", empresa="Oechsle", sede="Salaverry")
        sim = Sim([a], {"e1": cada(T(21, 8), T(21, 10)) + cada(T(21, 12), T(21, 14))})
        msgs = sim.correr(T(21, 8), T(21, 14))
        self.assertEqual(len(msgs), 2, [m.texto for _, m in msgs])
        (t1, abre), (t2, cierra) = msgs
        self.assertEqual(t1, T(21, 11))
        self.assertEqual((abre.nivel, abre.silencioso), (av.NARANJA, False))
        self.assertIn("e1 (Oechsle Salaverry · TG) sin datos desde las 09:59", abre.texto)
        self.assertTrue(cierra.silencioso)
        self.assertIn("volvió a las 12:00 (2 h 1 min sin datos)", cierra.texto)
        self.assertFalse(sim.estado.abiertos)

    def test_de_noche_espera_al_horario_y_lo_resuelto_no_se_avisa(self):
        a = U("e1", empresa="Oechsle", sede="Salaverry")
        b = U("e2", empresa="Scotiabank", sede="Jockey Plaza")
        lect = {"e1": cada(T(21, 20), T(21, 22)) + cada(T(21, 23, 30), T(22, 9)),
                "e2": cada(T(21, 20), T(22, 2))}
        msgs = Sim([a, b], lect).correr(T(21, 20), T(22, 9))
        self.assertEqual(len(msgs), 1, [m.texto for _, m in msgs])
        t, m = msgs[0]
        self.assertEqual(t, T(22, 8))
        self.assertIn("e2 (Scotiabank Jockey Plaza · TG) sin datos desde las 01:59 "
                      "(hace 6 h 1 min)", m.texto)

    def test_los_de_la_misma_sede_salen_en_un_mensaje(self):
        g1 = "Gateway 1 - PB Interior"
        a = U("a1", "ambiental", "Ducto", g1, cad=5)
        b = U("a2", "ambiental", "Zona Verde", g1, cad=5)
        lect = {"a1": cada(T(6, 0, 1), T(6, 2, 12), 5), "a2": cada(T(6, 0, 1), T(6, 2, 32), 5)}
        msgs = Sim([a, b], lect).correr(T(6, 0), T(6, 8, 5))
        self.assertEqual(len(msgs), 1)
        texto = msgs[0][1].texto
        self.assertTrue(texto.startswith("🟠 Sanna San Borja:"), texto)
        self.assertIn("• 🌡 Ducto sin datos desde las 02:11 (hace 5 h 49 min)", texto)
        self.assertIn("• 🌡 Zona Verde sin datos desde las 02:31", texto)

    def test_cortes_separados_no_son_corte_simultaneo(self):
        g = "Gateway 3"
        a, b = U("e1", gateway=g), U("e2", gateway=g)
        lect = {"e1": cada(T(21, 8), T(21, 10)), "e2": cada(T(21, 8), T(21, 10, 30))}
        msgs = Sim([a, b], lect).correr(T(21, 8), T(21, 12))
        self.assertEqual([m.nivel for _, m in msgs], [av.NARANJA, av.NARANJA])
        self.assertEqual([t for t, _ in msgs], [T(21, 11), T(21, 11, 30)])


class Grupos(unittest.TestCase):
    G2 = "Gateway 2 - PB Exterior"

    def test_gateway_caido_un_aviso_y_un_cierre(self):
        p3 = U("energia:3", nombre="Llave general cuarto de bombas", gateway=self.G2,
               grupo="TFC-BAF")
        sub = U("ambiental:147", "ambiental", "Subestación eléctrica", self.G2, cad=5)
        otro = U("energia:1", nombre="Llave general", gateway="Gateway 1")
        lect = {"energia:3": cada(T(15, 9), T(15, 11, 32)) + cada(T(16, 0, 54), T(16, 3)),
                "ambiental:147": cada(T(15, 9, 3), T(15, 11, 34), 5) + cada(T(16, 1, 1), T(16, 3), 5),
                "energia:1": cada(T(15, 9), T(16, 3))}
        sim = Sim([p3, sub, otro], lect)
        msgs = sim.correr(T(15, 9), T(16, 3))
        self.assertEqual(len(msgs), 2, [m.texto for _, m in msgs])
        (t1, abre), (_, cierra) = msgs
        self.assertEqual(t1, T(15, 11, 55))
        self.assertEqual((abre.nivel, abre.silencioso), (av.ROJO, False))
        self.assertIn("🔴 Gateway 2 - PB Exterior caído (Sanna San Borja)", abre.texto)
        self.assertIn("2 de 2 equipos sin datos desde las 11:31", abre.texto)
        self.assertIn("• Energía · TFC-BAF: Llave general cuarto de bombas", abre.texto)
        self.assertIn("• Salas: Subestación eléctrica", abre.texto)
        self.assertIn("Revisar el gateway", abre.texto)
        self.assertTrue(cierra.silencioso)
        self.assertIn("volvieron 2 de 2 equipos (13 h 30 min sin datos)", cierra.texto)
        self.assertEqual(sim.estado.abiertos, {})      # las unidades tapadas cerraron calladas

    def test_el_que_sigue_caido_hereda_el_aviso(self):
        g1 = "Gateway 1"
        us = [U(k, gateway=g1) for k in ("A", "B", "C")]
        lect = {"A": cada(T(21, 9), T(21, 10)) + cada(T(21, 11), T(21, 17)),
                "B": cada(T(21, 9), T(21, 10)) + cada(T(21, 11), T(21, 17)),
                "C": cada(T(21, 9), T(21, 10)) + cada(T(21, 15), T(21, 17))}
        sim = Sim(us, lect)
        msgs = sim.correr(T(21, 9), T(21, 17))
        textos = [m.texto for _, m in msgs]
        self.assertEqual(len(msgs), 3, textos)
        self.assertIn("🔴 Gateway 1 caído", textos[0])
        self.assertIn("volvieron 2 de 3 equipos", textos[1])
        self.assertIn("Sigue sin datos: C (desde las 09:59)", textos[1])
        self.assertIn("C (Sanna San Borja · TG) volvió a las 15:00 (5 h 1 min sin datos)",
                      textos[2])
        self.assertFalse(any(m.nivel == av.NARANJA and not m.silencioso for _, m in msgs))

    def test_plataforma_caida_un_solo_aviso(self):
        us = [U(f"x{i}", empresa="Oechsle", sede="Salaverry") for i in range(3)]
        us += [U(f"y{i}", empresa="Sanna", sede="San Borja") for i in range(2)]
        lect = {u.clave: cada(T(21, 9), T(21, 10)) + cada(T(21, 11), T(21, 12, 30)) for u in us}
        msgs = Sim(us, lect).correr(T(21, 9), T(21, 12, 30))
        textos = [m.texto for _, m in msgs]
        self.assertEqual(len(msgs), 2, textos)
        self.assertIn("🔴 Plataforma de energía sin datos: 5 de 5 equipos", textos[0])
        self.assertEqual(msgs[0][0], T(21, 10, 30))
        self.assertIn("✅ Plataforma de energía: volvieron los datos", textos[1])


class AntiRuido(unittest.TestCase):
    def test_dos_sueltos_en_un_gateway_grande_no_son_rojo(self):
        g1 = "Gateway 1 - PB Interior"
        salas = [U(f"a{i}", "ambiental", f"Sala {i}", g1, cad=5) for i in range(8)]
        lect = {u.clave: cada(T(21, 8, 1), T(21, 13), 5) for u in salas}
        lect["a0"] = cada(T(21, 8, 1), T(21, 9, 2), 5) + cada(T(21, 9, 40), T(21, 13), 5)
        lect["a1"] = cada(T(21, 8, 1), T(21, 9, 2), 5) + cada(T(21, 9, 40), T(21, 13), 5)
        msgs = Sim(salas, lect).correr(T(21, 8), T(21, 13))
        self.assertEqual(msgs, [])      # 38 min sin datos: ni 🔴 (2 de 8) ni 🟠 (< 60 min)

    def test_tres_juntos_si_y_dice_que_el_gateway_funciona(self):
        g1 = "Gateway 1 - PB Interior"
        eq = [U(f"e{i}", nombre=f"Equipo {i}", gateway=g1, grupo="TGA (N)") for i in range(3)]
        salas = [U(f"a{i}", "ambiental", f"Sala {i}", g1, cad=5) for i in range(5)]
        lect = {u.clave: cada(T(21, 8, 1), T(21, 12), 5) for u in salas}
        lect.update({u.clave: cada(T(21, 8), T(21, 9, 45)) + cada(T(21, 10, 15), T(21, 12))
                     for u in eq})
        msgs = Sim(eq + salas, lect).correr(T(21, 8), T(21, 12))
        self.assertEqual(len(msgs), 2, [m.texto for _, m in msgs])
        texto = msgs[0][1].texto
        self.assertIn("🔴 Gateway 1 - PB Interior (Sanna San Borja): 3 equipos se cortaron juntos",
                      texto)
        self.assertIn("• Energía · TGA (N): Equipo 0, Equipo 1, Equipo 2", texto)
        self.assertIn("Siguen reportando 5 salas de ambiental por el mismo gateway: el gateway "
                      "funciona", texto)

    def test_una_caida_vieja_no_cuenta_para_un_corte_nuevo(self):
        g2 = "Gateway 2"
        muerto = U("m", gateway=g2)
        vivo = U("v", gateway=g2)
        lect = {"m": cada(T(19, 8), T(19, 9)),                         # muerto hace 2 días
                "v": cada(T(19, 8), T(21, 10)) + cada(T(21, 11), T(21, 12))}
        sim = Sim([muerto, vivo], lect)
        sim.correr(T(21, 9), T(21, 12))
        self.assertFalse(any(e.inc.tipo == "grupo" for e in sim.eventos))

    def test_bateria_avisada_calla_sus_cortes_hasta_6_horas(self):
        z = U("a1", "ambiental", "Zona Verde", cad=5)
        lect = {"a1": cada(T(21, 8, 1), T(21, 9, 2), 5) + cada(T(21, 11, 1), T(21, 12), 5)
                + cada(T(21, 12, 1), T(21, 12, 30), 5) + cada(T(22, 0, 1), T(22, 1), 5)}
        msgs = Sim([z], lect).correr(T(21, 8), T(21, 20), extra=lambda k, t: {"bateria": 8.0})
        textos = [m.texto for _, m in msgs]
        self.assertEqual(len(msgs), 2, textos)
        self.assertIn("batería 8 %", textos[0])
        self.assertEqual(msgs[1][0], T(21, 18, 30))      # el corte de las 12:26 duró > 6 h
        self.assertIn("sin datos desde las 12:26 (hace 6 h 4 min); se cortó 2 veces seguidas; "
                      "ya tenía la batería baja", textos[1])

    def test_los_que_cruzan_casi_a_la_vez_van_juntos(self):
        a = U("e1", empresa="Oechsle", sede="Salaverry", gateway=None)
        b = U("e2", empresa="Oechsle", sede="Salaverry", gateway=None)
        otros = [U(f"x{i}", empresa="Oechsle", sede="Salaverry") for i in range(4)]
        lect = {"e1": cada(T(21, 8), T(21, 10)), "e2": cada(T(21, 8), T(21, 10, 8))}
        lect.update({u.clave: cada(T(21, 8), T(21, 13)) for u in otros})
        msgs = Sim([a, b] + otros, lect).correr(T(21, 8), T(21, 13))
        self.assertEqual(len(msgs), 1, [m.texto for _, m in msgs])
        self.assertIn("• ⚡ e1 (TG) sin datos desde las 09:59", msgs[0][1].texto)
        self.assertIn("• ⚡ e2 (TG) sin datos desde las 10:07", msgs[0][1].texto)

    def test_lo_que_volvio_de_madrugada_no_se_avisa_a_las_8(self):
        a = U("e1", empresa="Oechsle", sede="Salaverry")
        lect = {"e1": cada(T(21, 2), T(21, 3)) + cada(T(21, 7, 50), T(21, 10))}
        msgs = Sim([a], lect).correr(T(21, 2), T(21, 10))
        self.assertEqual(msgs, [])

    def test_corte_parcial_que_crece_avisa_una_vez_que_cayo_el_gateway(self):
        g1 = "Gateway 1"
        us = [U(f"e{i}", gateway=g1) for i in range(8)]
        lect = {u.clave: cada(T(21, 8), T(21, 9, 30)) for u in us[:3]}
        lect.update({u.clave: cada(T(21, 8), T(21, 10, 30)) for u in us[3:]})
        msgs = Sim(us, lect).correr(T(21, 8), T(21, 11, 30))
        textos = [m.texto for _, m in msgs]
        self.assertEqual(len(msgs), 2, textos)
        self.assertIn("3 equipos se cortaron juntos", textos[0])
        self.assertIn("🔴 Gateway 1 caído (Sanna San Borja)", textos[1])
        self.assertIn("8 de 8 equipos sin datos entre las 09:29 y las 10:29", textos[1])

    def test_si_recae_antes_de_2_horas_no_avisa_si_se_resuelve(self):
        a = U("e1", empresa="Oechsle", sede="Salaverry")
        lect = {"e1": cada(T(21, 8), T(21, 9)) + cada(T(21, 10, 30), T(21, 11, 10))
                + cada(T(21, 12, 40), T(21, 14))}
        msgs = Sim([a], lect).correr(T(21, 8), T(21, 14))
        self.assertEqual(len(msgs), 2, [m.texto for _, m in msgs])
        self.assertIn("sin datos desde las 08:59", msgs[0][1].texto)
        self.assertIn("volvió a las 10:30", msgs[1][1].texto)

    def test_si_recae_y_dura_2_horas_avisa(self):
        a = U("e1", empresa="Oechsle", sede="Salaverry")
        lect = {"e1": cada(T(21, 8), T(21, 9)) + cada(T(21, 10, 30), T(21, 11, 10))
                + cada(T(21, 14), T(21, 15))}
        msgs = Sim([a], lect).correr(T(21, 8), T(21, 15))
        textos = [m.texto for _, m in msgs]
        self.assertEqual(len(msgs), 4, textos)
        self.assertEqual(msgs[2][0], T(21, 13, 10))
        self.assertIn("sin datos desde las 11:09 (hace 2 h 1 min); se cortó 2 veces", textos[2])

    def test_mantenimiento_calla_hasta_que_termina(self):
        a = U("e1", empresa="Oechsle", sede="Salaverry")
        m = av.Mantenimiento("OECHSLE", hasta=T(21, 11, 30), desde=T(21, 9, 30))
        lect = {"e1": cada(T(21, 8), T(21, 10)) + cada(T(21, 13), T(21, 14))}
        msgs = Sim([a], lect, mants=[m]).correr(T(21, 8), T(21, 14))
        self.assertEqual(msgs[0][0], T(21, 11, 30))
        self.assertIn("sin datos desde las 09:59 (hace 1 h 31 min)", msgs[0][1].texto)

    def test_mantenimiento_por_texto(self):
        u = U("ambiental:184", "ambiental", "Zona Verde", "Gateway 1 - PB Interior", cad=5)
        self.assertEqual(av.unidades_en("zona verde", [u]), [u])
        self.assertEqual(av.unidades_en("gateway 1", [u]), [u])
        self.assertEqual(av.unidades_en("todo", [u]), [u])
        self.assertEqual(av.unidades_en("oechsle", [u]), [])


class Bateria(unittest.TestCase):
    def test_bateria_critica_y_cambio(self):
        z = U("a1", "ambiental", "Zona Verde", cad=5)
        lect = {"a1": cada(T(21, 7, 1), T(21, 16), 5)}
        extra = lambda k, t: {"bateria": 8.0 if t < T(21, 14) else 100.0}  # noqa: E731
        msgs = Sim([z], lect).correr(T(21, 8), T(21, 16), extra=extra)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0][1].texto,
                         "🟠🔋 🌡 Zona Verde (Sanna San Borja): batería 8 %, cambiarla pronto")
        self.assertIn("batería 100 % (la cambiaron)", msgs[1][1].texto)

    def test_entre_10_y_20_no_cierra_ni_abre(self):
        z = U("a1", "ambiental", "Zona Verde", cad=5)
        lect = {"a1": cada(T(21, 7, 1), T(21, 12), 5)}
        valores = iter([8.0] + [15.0] * 100)
        msgs = Sim([z], lect).correr(T(21, 8), T(21, 12),
                                     extra=lambda k, t: {"bateria": next(valores)})
        self.assertEqual(len(msgs), 1)


class NoMide(unittest.TestCase):
    def test_contador_quieto_frente_a_su_semana(self):
        e = U("e1", empresa="Oechsle", sede="Salaverry")
        lect = {"e1": cada(T(21, 7), T(21, 10))}
        msgs = Sim([e], lect).correr(T(21, 9), T(21, 9, 5), extra=lambda k, t: {
            "plano_h": 30.0, "plano_ref_h": 10.0})
        self.assertEqual(len(msgs), 1)
        self.assertIn("transmite pero no mide: el contador no avanza hace 1 día 6 h aunque "
                      "llegan lecturas (en su semana estuvo hasta 10 h sin avanzar)",
                      msgs[0][1].texto)
        msgs = Sim([e], lect).correr(T(21, 9), T(21, 9, 5), extra=lambda k, t: {
            "plano_h": 30.0, "plano_ref_h": 30.0})       # lo normal en su semana: no avisa
        self.assertEqual(msgs, [])

    def test_valores_fijos(self):
        s = U("a1", "ambiental", "Sala UPS", cad=5)
        lect = {"a1": cada(T(21, 7, 1), T(21, 10), 5)}
        msgs = Sim([s], lect).correr(T(21, 9), T(21, 9, 5), extra=lambda k, t: {
            "fijos": {"CO2": 3.5, "TEMPERATURE": 1.0}})
        self.assertIn("el CO₂ marca el mismo valor hace 3 h 30 min", msgs[0][1].texto)
        msgs = Sim([s], lect).correr(T(21, 9), T(21, 9, 5), extra=lambda k, t: {
            "fijos": {"TEMPERATURE": 10.7, "HUMIDITY": 2.0}})   # pasa en quirófanos: no avisa
        self.assertEqual(msgs, [])


class Monitor(unittest.TestCase):
    def test_base_que_no_se_lee_no_marca_equipos_caidos(self):
        e = U("e1", empresa="Oechsle", sede="Salaverry")
        lect = {"e1": cada(T(21, 8), T(21, 9))}
        sim = Sim([e], lect)
        err = lambda t: {"energia": "OperationalError: sin conexión"} if t < T(21, 10, 15) else None  # noqa: E731
        msgs = sim.correr(T(21, 9, 55), T(21, 10, 20), errores=err)
        textos = [m.texto for _, m in msgs]
        self.assertEqual(len(msgs), 3, textos)
        self.assertEqual(msgs[0][0], T(21, 10, 5))           # 3er ciclo seguido sin leer
        self.assertIn("⚙️ El monitor de avisos no puede leer la base de energía", textos[0])
        self.assertIn("no significa que los equipos estén caídos", textos[0])
        self.assertIn("volvió a leer", textos[1])
        self.assertIn("e1 (Oechsle Salaverry · TG) sin datos desde las 08:59", textos[2])


class Resumen(unittest.TestCase):
    def test_resumen_racha_y_escalado(self):
        r = R
        estado = av.Estado()
        verde = U("ambiental:184", "ambiental", "Zona Verde", "Gateway 1", cad=5)
        ok = U("energia:1", nombre="Llave general")
        nuevo = U("energia:233", nombre="Tablero existente de agencia", empresa="Scotiabank",
                  sede="Barranco")
        alta = datetime(2026, 10, 12, 10, tzinfo=LIMA)
        salidas = []
        for d in (6, 7, 8, 9):
            fecha = date(2026, 10, d)
            dias = [av.DiaUnidad(verde, cobertura=27.0, cortes=[(T(d, 2, 31), 159)], bateria=8.0),
                    av.DiaUnidad(ok, cobertura=100.0)]
            t = T(d + 1, 8)
            self.assertTrue(av.toca_resumen(estado, t, r))
            salidas.append(av.resumen_diario(estado, dias, fecha, t, r, [(nuevo, alta)]))
            self.assertFalse(av.toca_resumen(estado, t + timedelta(minutes=5), r))
        primero = salidas[0][0]
        self.assertEqual((primero.nivel, primero.silencioso, primero.tipo),
                         (av.AMARILLO, True, "resumen"))
        self.assertIn("🟡 Resumen ZEIA de ayer, martes 6 oct", primero.texto)
        self.assertIn("• 🌡 Zona Verde (Sanna San Borja): 27% de lecturas · pierde lecturas "
                      "sueltas (enlace débil) · sin datos 02:31–05:10 (2 h 39 min)", primero.texto)
        self.assertIn("🔋 Batería baja (<20 %): Zona Verde 8 %", primero.texto)
        self.assertIn("Sin primera lectura (1): Tablero existente de agencia", primero.texto)
        self.assertNotIn("Sin primera lectura", salidas[1][0].texto)   # solo si cambia o lunes
        self.assertEqual([len(s) for s in salidas], [1, 1, 2, 1])
        esc = salidas[2][1]
        self.assertEqual((esc.nivel, esc.silencioso), (av.NARANJA, False))
        self.assertIn("Zona Verde (Sanna San Borja) lleva 3 días seguidos con problemas: "
                      "mar 27% · mié 27% · jue 27%", esc.texto)

    def test_dia_sin_problemas(self):
        estado = av.Estado()
        ok = U("energia:1", nombre="Llave general")
        msgs = av.resumen_diario(estado, [av.DiaUnidad(ok, cobertura=99.5)], date(2026, 10, 20),
                                 T(21, 8), R)
        self.assertIn("✅ Ayer todo bien: 1 reportaron ≥ 90 %", msgs[0].texto)
        self.assertIn("Vigilados: 1 equipo de energía.", msgs[0].texto)

    def test_snr_y_datos_raros(self):
        roja = U("ambiental:186", "ambiental", "Zona Roja", cad=5)
        d = av.DiaUnidad(roja, cobertura=99.0, snr=-12.0, snr_ref=10.2,
                         raros=["el CO₂ marcó 0 ppm (imposible)"])
        p = av.problemas(d)
        self.assertEqual(p, ["SNR -12 dB (su semana +10 dB): enlace de radio débil",
                             "el CO₂ marcó 0 ppm (imposible)"])


class Persistencia(unittest.TestCase):
    def test_estado_ida_y_vuelta(self):
        e = av.Estado(ultimo_resumen="2026-10-21", sin_primera=["energia:233"])
        e.abiertos["sin_datos|e1"] = av.Incidente(
            "sin_datos|e1", "sin_datos", av.NARANJA, abierto=T(21, 11), desde=T(21, 9, 59),
            nombre="e1", tienda="Oechsle Salaverry", grupo="TG", modulo="energia",
            unidades=["e1"], avisado=True, notificar_desde=T(21, 13))
        e.cerrados["x"] = {"t": T(21, 8).isoformat(), "cortes": 2}
        otra = av.Estado.de_json(e.a_json())
        inc = otra.abiertos["sin_datos|e1"]
        self.assertEqual((inc.desde, inc.notificar_desde, inc.avisado), (T(21, 9, 59), T(21, 13), True))
        self.assertEqual(otra.cerrados, e.cerrados)
        self.assertEqual(otra.sin_primera, ["energia:233"])
        self.assertEqual(av.Estado.de_json(None).abiertos, {})


if __name__ == "__main__":
    unittest.main()
