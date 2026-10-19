"""Cliente mínimo de la Bot API de Telegram para los avisos del equipo.

Usa httpx (ya instalado como dependencia de openai). El token NUNCA se
muestra: todos los errores salen como TelegramError con el token tapado.

Configuración en .env:
    TELEGRAM_BOT_TOKEN=123456789:AA...   (lo entrega @BotFather)
    TELEGRAM_CHAT_ID=-100...             (id del grupo; lo muestra
                                          scripts/telegram_setup.py)
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from . import config

API_URL = "https://api.telegram.org"
TIMEOUT = 20.0
TIMEOUT_FOTO = 60.0       # subir una imagen puede tardar más
MAX_CHARS = 4096          # límite de Telegram por mensaje
MAX_CAPTION = 1024        # límite de Telegram para el texto de una foto

# Solo para pruebas: se reemplaza por httpx.MockTransport (sin red).
_transport: Optional[httpx.BaseTransport] = None


class TelegramError(RuntimeError):
    """Error legible de Telegram (falta de token, chat inválido, red, etc.)."""


def _token() -> str:
    if not config.TELEGRAM_BOT_TOKEN:
        raise TelegramError("Falta TELEGRAM_BOT_TOKEN en .env (lo entrega @BotFather)")
    return config.TELEGRAM_BOT_TOKEN


def _tapar(texto: str, token: str) -> str:
    """Reemplaza el token por *** (los errores de red pueden traer la URL)."""
    return texto.replace(token, "***") if token else texto


def _destino(chat_id: Optional[str]) -> str:
    destino = str(chat_id or config.TELEGRAM_CHAT_ID or "").strip()
    if not destino:
        raise TelegramError("Falta TELEGRAM_CHAT_ID en .env "
                            "(lo muestra: python scripts/telegram_setup.py)")
    return destino


def _recortar(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[:limite - 1] + "…"


def _respuesta(reply_to: Optional[int]) -> dict:
    """Responder a un mensaje (si lo borraron, igual se envía)."""
    if not reply_to:
        return {}
    return {"reply_parameters": {"message_id": int(reply_to),
                                 "allow_sending_without_reply": True}}


def _call(method: str, payload: Optional[dict] = None, files: Optional[dict] = None,
          timeout: float = TIMEOUT):
    """Llama a un método de la Bot API y devuelve su `result`.

    Con `files` el pedido va como multipart (los campos no-texto en JSON).
    """
    token = _token()
    url = f"{API_URL}/bot{token}/{method}"
    try:
        with httpx.Client(timeout=timeout, transport=_transport) as client:
            if files:
                data = {k: v if isinstance(v, str) else json.dumps(v)
                        for k, v in (payload or {}).items()}
                resp = client.post(url, data=data, files=files)
            else:
                resp = client.post(url, json=payload or {})
    except httpx.HTTPError as e:
        # `from None`: que el traceback original (con la URL) no se muestre.
        raise TelegramError(
            f"No se pudo conectar con Telegram: {_tapar(str(e), token)}") from None
    try:
        data = resp.json()
    except ValueError:
        raise TelegramError(f"Telegram respondió {resp.status_code} sin JSON") from None
    if data.get("ok"):
        return data.get("result")
    params = data.get("parameters") or {}
    if params.get("migrate_to_chat_id"):
        raise TelegramError(
            "El grupo se convirtió en supergrupo y cambió de id: pon "
            f"TELEGRAM_CHAT_ID={params['migrate_to_chat_id']} en .env")
    if resp.status_code == 401:
        raise TelegramError(
            "Token inválido: revisa TELEGRAM_BOT_TOKEN en .env (cópialo de @BotFather)")
    desc = _tapar(str(data.get("description") or "error desconocido"), token)
    raise TelegramError(f"Telegram respondió {resp.status_code}: {desc}")


def get_me() -> dict:
    """Datos del bot (sirve para validar el token)."""
    return _call("getMe")


def get_updates(offset: Optional[int] = None, timeout: int = 0,
                allowed_updates: Optional[list] = None) -> list:
    """Mensajes/eventos pendientes del bot (Telegram los guarda ~24 h).

    offset: confirma todo lo anterior (update_id + 1 del último procesado).
    timeout: segundos de long polling (Telegram responde apenas llega algo).
    allowed_updates: tipos a recibir ([] = los de siempre). Telegram RECUERDA
    el último valor, así que conviene pasarlo siempre.
    """
    payload: dict = {"timeout": int(timeout)}
    if offset is not None:
        payload["offset"] = int(offset)
    if allowed_updates is not None:
        payload["allowed_updates"] = list(allowed_updates)
    return _call("getUpdates", payload, timeout=TIMEOUT + timeout) or []


def chats_from_updates(updates: list) -> list:
    """Chats únicos donde el bot recibió algo o fue agregado/quitado.

    Devuelve [{id, tipo, nombre, activo}]; `activo` = False si después
    sacaron al bot o el grupo se convirtió en supergrupo (id nuevo).
    """
    chats: dict = {}
    for u in updates or []:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            ev = u.get(key) or {}
            chat = ev.get("chat") or {}
            if "id" not in chat:
                continue
            nombre = (chat.get("title")
                      or " ".join(x for x in (chat.get("first_name"),
                                              chat.get("last_name")) if x)
                      or chat.get("username") or "")
            activo = True
            if key == "my_chat_member":
                estado = (ev.get("new_chat_member") or {}).get("status")
                activo = estado not in ("left", "kicked")
            if ev.get("migrate_to_chat_id"):
                activo = False
            chats[chat["id"]] = {"id": chat["id"], "tipo": chat.get("type", ""),
                                 "nombre": nombre, "activo": activo}
    return list(chats.values())


def send_message(text: str, chat_id: Optional[str] = None, silent: bool = False,
                 html: bool = False, reply_to: Optional[int] = None) -> dict:
    """Envía un mensaje al grupo de avisos.

    silent=True → llega sin sonido (resumen 🟡 y cierres fuera de horario).
    html=True   → permite <b>, <i>, <code> (el texto debe venir escapado).
    reply_to    → id del mensaje al que responde (p. ej. el comando).
    """
    payload = {"chat_id": _destino(chat_id), "text": _recortar(text, MAX_CHARS),
               "disable_notification": bool(silent),
               "link_preview_options": {"is_disabled": True},
               **_respuesta(reply_to)}
    if html:
        payload["parse_mode"] = "HTML"
    return _call("sendMessage", payload)


def send_photo(png: bytes, caption: str = "", chat_id: Optional[str] = None,
               silent: bool = False, reply_to: Optional[int] = None,
               filename: str = "foto.png") -> dict:
    """Envía una imagen PNG con su texto (máx. 1,024 caracteres)."""
    payload = {"chat_id": _destino(chat_id), "caption": _recortar(caption, MAX_CAPTION),
               "disable_notification": bool(silent), **_respuesta(reply_to)}
    return _call("sendPhoto", payload, files={"photo": (filename, png, "image/png")},
                 timeout=TIMEOUT_FOTO)


def send_chat_action(action: str = "typing", chat_id: Optional[str] = None) -> bool:
    """Muestra "enviando foto…" / "escribiendo…" mientras se prepara la respuesta."""
    return _call("sendChatAction", {"chat_id": _destino(chat_id), "action": action})


def set_my_commands(commands: list, chat_id: Optional[str] = None) -> bool:
    """Registra el menú de comandos (lo que sale al escribir "/") solo en el grupo.

    commands: [(comando, descripción), ...] sin la barra.
    """
    destino = _destino(chat_id)
    scope_id = int(destino) if destino.lstrip("-").isdigit() else destino
    return _call("setMyCommands", {
        "commands": [{"command": c, "description": d} for c, d in commands],
        "scope": {"type": "chat", "chat_id": scope_id},
    })
