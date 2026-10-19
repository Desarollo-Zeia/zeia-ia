"""Pruebas del cliente de Telegram (sin red: httpx.MockTransport).

    python -m unittest tests.test_telegram -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, telegram  # noqa: E402

TOKEN = "123456:ABC-token-secreto"
CHAT = "-100777"


class TelegramTest(unittest.TestCase):
    def setUp(self):
        self.pedidos = []
        for nombre, valor in (("TELEGRAM_BOT_TOKEN", TOKEN), ("TELEGRAM_CHAT_ID", CHAT)):
            p = mock.patch.object(config, nombre, valor)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, telegram, "_transport", None)

    def responder(self, status: int = 200, body: dict | None = None, error=None):
        """Simula a Telegram: guarda cada pedido y responde `body` (o falla)."""
        def handler(request: httpx.Request) -> httpx.Response:
            self.pedidos.append(request)
            if error is not None:
                raise error
            return httpx.Response(status, json=body or {"ok": True, "result": {"message_id": 1}})
        telegram._transport = httpx.MockTransport(handler)

    def enviado(self) -> dict:
        return json.loads(self.pedidos[-1].content)

    # ---------- envío ----------

    def test_envio_normal_suena_y_va_al_grupo_configurado(self):
        self.responder()
        telegram.send_message("hola")
        self.assertTrue(self.pedidos[-1].url.path.endswith("/sendMessage"))
        p = self.enviado()
        self.assertEqual(p["chat_id"], CHAT)
        self.assertEqual(p["text"], "hola")
        self.assertFalse(p["disable_notification"])
        self.assertNotIn("parse_mode", p)

    def test_envio_silencioso_y_html(self):
        self.responder()
        telegram.send_message("<b>Zona Roja</b>", silent=True, html=True)
        p = self.enviado()
        self.assertTrue(p["disable_notification"])
        self.assertEqual(p["parse_mode"], "HTML")

    def test_texto_largo_se_recorta_al_limite_de_telegram(self):
        self.responder()
        telegram.send_message("x" * 5000)
        self.assertEqual(len(self.enviado()["text"]), telegram.MAX_CHARS)

    def test_chat_explicito_reemplaza_al_de_env(self):
        self.responder()
        telegram.send_message("hola", chat_id="-100999")
        self.assertEqual(self.enviado()["chat_id"], "-100999")

    # ---------- configuración faltante ----------

    def test_sin_token_error_claro_y_sin_llamar_a_telegram(self):
        self.responder()
        with mock.patch.object(config, "TELEGRAM_BOT_TOKEN", ""):
            with self.assertRaisesRegex(telegram.TelegramError, "TELEGRAM_BOT_TOKEN"):
                telegram.get_me()
        self.assertEqual(self.pedidos, [])

    def test_sin_chat_id_error_claro(self):
        self.responder()
        with mock.patch.object(config, "TELEGRAM_CHAT_ID", ""):
            with self.assertRaisesRegex(telegram.TelegramError, "TELEGRAM_CHAT_ID"):
                telegram.send_message("hola")
        self.assertEqual(self.pedidos, [])

    # ---------- errores de Telegram (sin filtrar el token) ----------

    def test_token_invalido(self):
        self.responder(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
        with self.assertRaises(telegram.TelegramError) as ctx:
            telegram.get_me()
        self.assertIn("Token inválido", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_grupo_convertido_en_supergrupo_indica_el_id_nuevo(self):
        self.responder(400, {"ok": False, "error_code": 400,
                             "description": "Bad Request: group chat was upgraded to a supergroup chat",
                             "parameters": {"migrate_to_chat_id": -1001234567890}})
        with self.assertRaisesRegex(telegram.TelegramError, "TELEGRAM_CHAT_ID=-1001234567890"):
            telegram.send_message("hola")

    def test_otro_error_trae_la_descripcion(self):
        self.responder(400, {"ok": False, "error_code": 400,
                             "description": "Bad Request: chat not found"})
        with self.assertRaisesRegex(telegram.TelegramError, "chat not found"):
            telegram.send_message("hola")

    def test_error_de_red_no_muestra_el_token(self):
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        self.responder(error=httpx.ConnectError(f"sin conexión a {url}"))
        with self.assertRaises(telegram.TelegramError) as ctx:
            telegram.get_me()
        self.assertIn("No se pudo conectar", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    # ---------- chats vistos en getUpdates ----------

    def test_chats_desde_updates(self):
        updates = [
            # bot agregado a un grupo
            {"update_id": 1, "my_chat_member": {
                "chat": {"id": -4001, "type": "group", "title": "ZEIA Alertas"},
                "new_chat_member": {"status": "member"}}},
            # mensaje privado al bot
            {"update_id": 2, "message": {
                "chat": {"id": 55, "type": "private", "first_name": "Ana", "last_name": "P"}}},
            # bot agregado y luego sacado de otro grupo
            {"update_id": 3, "my_chat_member": {
                "chat": {"id": -4002, "type": "group", "title": "Pruebas"},
                "new_chat_member": {"status": "member"}}},
            {"update_id": 4, "my_chat_member": {
                "chat": {"id": -4002, "type": "group", "title": "Pruebas"},
                "new_chat_member": {"status": "kicked"}}},
            # el primer grupo pasó a supergrupo (id nuevo)
            {"update_id": 5, "message": {
                "chat": {"id": -4001, "type": "group", "title": "ZEIA Alertas"},
                "migrate_to_chat_id": -1004001}},
            {"update_id": 6, "message": {
                "chat": {"id": -1004001, "type": "supergroup", "title": "ZEIA Alertas"},
                "migrate_from_chat_id": -4001}},
        ]
        chats = {c["id"]: c for c in telegram.chats_from_updates(updates)}
        self.assertEqual(set(chats), {-4001, 55, -4002, -1004001})
        self.assertFalse(chats[-4001]["activo"])       # migrado
        self.assertFalse(chats[-4002]["activo"])       # sacaron al bot
        self.assertTrue(chats[-1004001]["activo"])
        self.assertEqual(chats[-1004001]["tipo"], "supergroup")
        self.assertEqual(chats[55]["nombre"], "Ana P")

    def test_get_updates_vacio(self):
        self.responder(200, {"ok": True, "result": []})
        self.assertEqual(telegram.get_updates(), [])

    # ---------- bot de comandos ----------

    def test_get_updates_con_long_polling(self):
        self.responder(200, {"ok": True, "result": []})
        telegram.get_updates(offset=42, timeout=50, allowed_updates=["message"])
        self.assertEqual(self.enviado(), {"timeout": 50, "offset": 42,
                                          "allowed_updates": ["message"]})

    def test_responder_a_un_mensaje(self):
        self.responder()
        telegram.send_message("hola", reply_to=77)
        self.assertEqual(self.enviado()["reply_parameters"],
                         {"message_id": 77, "allow_sending_without_reply": True})

    def test_send_photo_va_como_multipart(self):
        self.responder()
        telegram.send_photo(b"\x89PNG-bytes", "🌡 Salas", reply_to=77, silent=True)
        req = self.pedidos[-1]
        self.assertTrue(req.url.path.endswith("/sendPhoto"))
        self.assertIn("multipart/form-data", req.headers["content-type"])
        cuerpo = req.content
        self.assertIn(b"\x89PNG-bytes", cuerpo)
        self.assertIn('🌡 Salas'.encode(), cuerpo)
        self.assertIn(CHAT.encode(), cuerpo)
        self.assertIn(b'{"message_id": 77, "allow_sending_without_reply": true}', cuerpo)
        self.assertIn(b"true", cuerpo)            # disable_notification
        self.assertIn(b'filename="foto.png"', cuerpo)

    def test_caption_largo_se_recorta(self):
        self.responder()
        telegram.send_photo(b"x", "y" * 2000)
        self.assertIn(("y" * (telegram.MAX_CAPTION - 1) + "…").encode(), self.pedidos[-1].content)
        self.assertNotIn(("y" * telegram.MAX_CAPTION).encode(), self.pedidos[-1].content)

    def test_menu_de_comandos_solo_en_el_grupo(self):
        self.responder(200, {"ok": True, "result": True})
        telegram.set_my_commands([("salas", "Foto de las salas")])
        self.assertEqual(self.enviado(), {
            "commands": [{"command": "salas", "description": "Foto de las salas"}],
            "scope": {"type": "chat", "chat_id": int(CHAT)}})

    def test_accion_de_chat(self):
        self.responder(200, {"ok": True, "result": True})
        telegram.send_chat_action("upload_photo")
        self.assertEqual(self.enviado(), {"chat_id": CHAT, "action": "upload_photo"})


if __name__ == "__main__":
    unittest.main()
