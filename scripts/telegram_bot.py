"""Bot de Telegram del equipo ZEIA: responde comandos en el grupo de avisos.

    scripts\\telegram_bot.cmd            lo arranca leyendo producción en vivo
    python scripts/telegram_bot.py      lo mismo con la config de .env

Comandos (solo en el grupo de TELEGRAM_CHAT_ID; lo demás se ignora). Cada
uno manda una foto con SOLO lo que tuvo problemas hoy, de todas las
empresas juntas y de peor a mejor (src/foto_salas.py):
    /ambiental   salas (CO2, temperatura, humedad)
    /energia     equipos de energía
    /agua        medidores de agua
    /ayuda       lista de comandos

Una sola instancia por PC (candado en 127.0.0.1:47831).
"""
from __future__ import annotations

import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, telegram, tunnel  # noqa: E402

# La consola de Windows puede no soportar emojis: que nunca rompa la salida.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

MODULOS = ("ambiental", "energia", "agua")
COMANDOS = [("ambiental", "Salas con problemas hoy (CO2, temperatura, humedad)"),
            ("energia", "Equipos de energía con problemas hoy"),
            ("agua", "Medidores de agua con problemas hoy"),
            ("ayuda", "Qué comandos hay")]
ALIAS = {"ambiental": "ambiental", "energia": "energia", "agua": "agua",
         "salas": "ambiental",                     # el comando de la 1a versión
         "ayuda": "ayuda", "help": "ayuda", "start": "ayuda"}
AYUDA = ("Soy el bot de avisos de ZEIA. Cada comando manda una foto con SOLO lo que "
         "tuvo problemas hoy, de todas las empresas, de peor a mejor:\n"
         "/ambiental: salas (CO2, temperatura, humedad)\n"
         "/energia: equipos de energía\n"
         "/agua: medidores de agua\n"
         "En la barra del día: verde = llegaron lecturas, ámbar = hubo cortes, "
         "rojo = no llegó nada. Si todo va bien, responde solo con un mensaje.")
MAX_EDAD_S = 300        # comandos de cuando el bot estaba apagado: se ignoran
POLL_S = 50             # long polling: Telegram responde apenas llega algo
PUERTO_CANDADO = 47831  # candado local: una sola instancia por PC


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def _corto(ex: Exception) -> str:
    txt = (str(ex).strip().splitlines() or [type(ex).__name__])[0]
    return txt[:200]


def interpretar(update: dict, usuario: str, chat_id: str,
                ahora: float) -> Optional[tuple]:
    """(acción, id del mensaje) si es un comando válido para este bot; si no, None.

    Solo acepta mensajes del grupo configurado, de personas (no bots), que
    empiecen con "/", dirigidos a este bot (o sin @) y de menos de MAX_EDAD_S.
    """
    msg = update.get("message") or {}
    if not msg or str((msg.get("chat") or {}).get("id")) != str(chat_id):
        return None
    if (msg.get("from") or {}).get("is_bot"):
        return None
    texto = (msg.get("text") or "").strip()
    if not texto.startswith("/"):
        return None
    if ahora - int(msg.get("date") or 0) > MAX_EDAD_S:
        return None
    comando, _, destino = texto.split()[0][1:].partition("@")
    if destino and destino.lower() != usuario.lower():
        return None
    accion = ALIAS.get(comando.lower())
    return (accion, msg.get("message_id")) if accion else None


def responder(accion: str, msg_id: Optional[int]) -> None:
    if accion == "ayuda":
        telegram.send_message(AYUDA, reply_to=msg_id)
        return
    if accion not in MODULOS:
        return
    from src.foto_salas import foto_peores   # import tardío: Playwright pesa
    try:
        telegram.send_chat_action("upload_photo")
    except telegram.TelegramError:
        pass
    t0 = time.time()
    try:
        foto = foto_peores(accion)
    except Exception as ex:  # noqa: BLE001 - se informa en el grupo
        log(f"/{accion} falló: {type(ex).__name__}: {_corto(ex)}")
        telegram.send_message(f"No pude armar la foto de /{accion}: {_corto(ex)}",
                              reply_to=msg_id)
        return
    if foto.png is None:
        telegram.send_message(foto.caption, reply_to=msg_id)
    else:
        telegram.send_photo(foto.png, foto.caption, reply_to=msg_id, filename=f"{accion}.png")
    log(f"/{accion} respondido en {time.time() - t0:.1f} s "
        f"({len(foto.problemas)} con problemas de {len(foto.filas)})")


def _candado() -> Optional[socket.socket]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PUERTO_CANDADO))
    except OSError:
        s.close()
        return None
    return s


def main() -> int:
    candado = _candado()
    if candado is None:
        print("Ya hay un bot corriendo en esta PC (puerto 127.0.0.1:"
              f"{PUERTO_CANDADO} ocupado). Ciérralo antes de abrir otro.")
        return 1
    try:
        if not config.TELEGRAM_CHAT_ID:
            print("Falta TELEGRAM_CHAT_ID en .env: corre scripts/telegram_setup.py")
            return 1
        try:
            usuario = telegram.get_me()["username"]
            telegram.set_my_commands(COMANDOS)
        except telegram.TelegramError as ex:
            print(f"ERROR: {ex}")
            return 1
        log(f"Bot @{usuario} escuchando en el grupo {config.TELEGRAM_CHAT_ID} "
            "(Ctrl+C para salir)")
        offset, espera = None, 5
        while True:
            try:
                updates = telegram.get_updates(offset=offset, timeout=POLL_S,
                                               allowed_updates=["message"])
                espera = 5
            except telegram.TelegramError as ex:
                if " 409" in str(ex):
                    log("Otro proceso está leyendo este bot (¿otra PC?). Reintento en 60 s.")
                    time.sleep(60)
                else:
                    log(f"Telegram: {ex}. Reintento en {espera} s.")
                    time.sleep(espera)
                    espera = min(espera * 2, 300)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                orden = interpretar(u, usuario, config.TELEGRAM_CHAT_ID, time.time())
                if not orden:
                    continue
                accion, msg_id = orden
                log(f"/{accion} pedido (mensaje {msg_id})")
                try:
                    responder(accion, msg_id)
                except telegram.TelegramError as ex:
                    log(f"No pude responder /{accion}: {ex}")
    except KeyboardInterrupt:
        log("Bot detenido.")
        return 0
    finally:
        tunnel.close_tunnel()   # solo cierra los túneles que abrió este proceso
        candado.close()


if __name__ == "__main__":
    sys.exit(main())
