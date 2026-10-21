"""Pruebas del bot de comandos (sin red ni base de datos).

    python -m unittest tests.test_telegram_bot -v
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

RUTA = Path(__file__).resolve().parent.parent / "scripts" / "telegram_bot.py"
_spec = importlib.util.spec_from_file_location("telegram_bot", RUTA)
bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot)

GRUPO = "-5584536516"
AHORA = 1_800_000_000


def upd(texto, chat=GRUPO, edad=10, es_bot=False, clave="message"):
    return {"update_id": 1, clave: {
        "message_id": 77, "date": AHORA - edad, "text": texto,
        "chat": {"id": int(chat), "type": "group"},
        "from": {"id": 5, "is_bot": es_bot, "first_name": "Ana"}}}


def leer(update):
    return bot.interpretar(update, "zeia_alerts_bot", GRUPO, AHORA)


class Interpretar(unittest.TestCase):
    def test_los_tres_comandos(self):
        for modulo in ("ambiental", "energia", "agua"):
            self.assertEqual(leer(upd(f"/{modulo}")), (modulo, 77))

    def test_salas_sigue_siendo_ambiental(self):
        self.assertEqual(leer(upd("/salas")), ("ambiental", 77))

    def test_comando_dirigido_a_este_bot(self):
        self.assertEqual(leer(upd("/energia@ZEIA_alerts_bot")), ("energia", 77))

    def test_comando_para_otro_bot_se_ignora(self):
        self.assertIsNone(leer(upd("/energia@otro_bot")))

    def test_otro_chat_se_ignora(self):
        # cualquiera puede escribirle al bot por privado: no debe recibir datos
        self.assertIsNone(leer(upd("/agua", chat="123456")))

    def test_comando_viejo_se_ignora(self):
        self.assertIsNone(leer(upd("/agua", edad=bot.MAX_EDAD_S + 1)))

    def test_texto_normal_y_comando_desconocido_se_ignoran(self):
        self.assertIsNone(leer(upd("hola /agua")))
        self.assertIsNone(leer(upd("/borrar_todo")))

    def test_mensajes_de_bots_y_editados_se_ignoran(self):
        self.assertIsNone(leer(upd("/agua", es_bot=True)))
        self.assertIsNone(leer(upd("/agua", clave="edited_message")))

    def test_ayuda_y_sus_alias(self):
        for txt in ("/ayuda", "/help", "/start", "/start@zeia_alerts_bot alertas"):
            self.assertEqual(leer(upd(txt)), ("ayuda", 77), txt)

    def test_menu_con_los_modulos_avisos_y_ayuda(self):
        self.assertEqual([c for c, _ in bot.COMANDOS],
                         ["ambiental", "energia", "agua", "avisos", "mantenimiento", "ayuda"])

    def test_avisos_y_mantenimiento_con_argumentos(self):
        self.assertEqual(leer(upd("/avisos")), ("avisos", 77))
        u = upd("/mantenimiento@zeia_alerts_bot Gateway 2   3h")
        self.assertEqual(leer(u), ("mantenimiento", 77))
        self.assertEqual(bot.argumentos(u), "Gateway 2   3h")
        self.assertEqual(bot.argumentos(upd("/mant")), "")
        self.assertEqual(bot.autor(u), "Ana")


class Responder(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(bot, "log")   # sin ruido en la salida de las pruebas
        p.start()
        self.addCleanup(p.stop)

    def test_avisos_responde_el_estado(self):
        with mock.patch("src.avisos_vivo.texto_estado", return_value="🔔 estado") as est, \
             mock.patch.object(bot.telegram, "send_message") as msg:
            bot.responder("avisos", 77)
        est.assert_called_once_with()
        msg.assert_called_once_with("🔔 estado", reply_to=77)

    def test_mantenimiento_pasa_argumentos_y_autor(self):
        with mock.patch("src.avisos_vivo.comando_mantenimiento", return_value="🛠 ok") as cm, \
             mock.patch.object(bot.telegram, "send_message") as msg:
            bot.responder("mantenimiento", 77, "Sanna 2h", "Ana")
        cm.assert_called_once_with("Sanna 2h", por="Ana")
        msg.assert_called_once_with("🛠 ok", reply_to=77)

    def test_manda_la_foto_del_modulo_respondiendo_al_comando(self):
        foto = mock.Mock(png=b"PNG", caption="⚡ Energía", filas=[1, 2], problemas=[1])
        with mock.patch("src.foto_salas.foto_peores", return_value=foto) as peores, \
             mock.patch.object(bot.telegram, "send_chat_action") as accion, \
             mock.patch.object(bot.telegram, "send_photo") as foto_tg:
            bot.responder("energia", 77)
        peores.assert_called_once_with("energia")
        accion.assert_called_once_with("upload_photo")
        foto_tg.assert_called_once_with(b"PNG", "⚡ Energía", reply_to=77, filename="energia.png")

    def test_si_todo_va_bien_manda_solo_texto(self):
        foto = mock.Mock(png=None, caption="✅ Todo bien", filas=[1], problemas=[])
        with mock.patch("src.foto_salas.foto_peores", return_value=foto), \
             mock.patch.object(bot.telegram, "send_chat_action"), \
             mock.patch.object(bot.telegram, "send_message") as msg, \
             mock.patch.object(bot.telegram, "send_photo") as foto_tg:
            bot.responder("agua", 77)
        msg.assert_called_once_with("✅ Todo bien", reply_to=77)
        foto_tg.assert_not_called()

    def test_si_falla_la_base_avisa_en_el_grupo(self):
        with mock.patch("src.foto_salas.foto_peores",
                        side_effect=RuntimeError("túnel caído\ndetalle")), \
             mock.patch.object(bot.telegram, "send_chat_action"), \
             mock.patch.object(bot.telegram, "send_message") as msg:
            bot.responder("ambiental", 77)
        msg.assert_called_once_with("No pude armar la foto de /ambiental: túnel caído",
                                    reply_to=77)


if __name__ == "__main__":
    unittest.main()
