"""Configura y prueba el bot de Telegram de los avisos del monitor.

    python scripts/telegram_setup.py          valida el token y muestra el id del grupo
    python scripts/telegram_setup.py --test   manda 2 mensajes de prueba al grupo

Antes: crear el bot con @BotFather, poner TELEGRAM_BOT_TOKEN en .env, agregar
el bot al grupo del equipo y escribir /start@<usuario_del_bot> en el grupo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, telegram  # noqa: E402

# La consola de Windows puede no soportar emojis: que nunca rompa la salida.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def _mostrar_chats(usuario: str) -> int:
    # allowed_updates=[]: vuelve a los tipos de siempre (el bot de comandos
    # solo pide "message" y Telegram recuerda la última elección).
    chats = telegram.chats_from_updates(telegram.get_updates(allowed_updates=[]))
    grupos = [c for c in chats if c["tipo"] in ("group", "supergroup")]
    activos = [c for c in grupos if c["activo"]]

    if not grupos:
        print("\nTodavía no veo ningún grupo. En el grupo del equipo:")
        print("  1. Agrega al bot (búscalo como @" + usuario + ").")
        print("  2. Escribe en el grupo:  /start@" + usuario)
        print("  3. Vuelve a correr este script (Telegram guarda esos mensajes ~24 h).")
        return 1

    print("\nGrupos donde está el bot:")
    for c in grupos:
        nota = "" if c["activo"] else "   (ya no sirve: sacaron al bot o cambió de id)"
        print(f"  {c['id']:>16}  {c['tipo']:<10}  {c['nombre']}{nota}")

    actual = config.TELEGRAM_CHAT_ID
    if actual:
        if any(str(c["id"]) == actual for c in activos):
            print(f"\nTELEGRAM_CHAT_ID={actual} ya está en .env y es correcto.")
            print("Siguiente paso:  python scripts/telegram_setup.py --test")
            return 0
        print(f"\nOjo: TELEGRAM_CHAT_ID={actual} (en .env) no coincide con los grupos de arriba.")

    if len(activos) == 1:
        print("\nAgrega esta línea a .env:")
        print(f"  TELEGRAM_CHAT_ID={activos[0]['id']}")
    else:
        print("\nAgrega a .env el id del grupo de avisos:  TELEGRAM_CHAT_ID=<id>")
    print("Luego prueba el envío:  python scripts/telegram_setup.py --test")
    return 0


def _prueba() -> int:
    if not config.TELEGRAM_CHAT_ID:
        print("Falta TELEGRAM_CHAT_ID en .env: corre primero el script sin --test.")
        return 1
    telegram.send_message("🔴 Prueba ZEIA: así llega un aviso urgente (con sonido).")
    telegram.send_message("🟡 Prueba ZEIA: así llega un aviso del resumen (sin sonido).",
                          silent=True)
    print("Listo: revisa el grupo. El primer mensaje debió sonar; el segundo llega en silencio.")
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--test", action="store_true",
                    help="manda 2 mensajes de prueba al grupo (TELEGRAM_CHAT_ID)")
    args = ap.parse_args(argv)
    try:
        bot = telegram.get_me()
        usuario = bot.get("username", "")
        print(f"Token OK: bot @{usuario} ({bot.get('first_name', '')})")
        return _prueba() if args.test else _mostrar_chats(usuario)
    except telegram.TelegramError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
