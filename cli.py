#!/usr/bin/env python
"""Chat en terminal con el agente (módulos energía / ambiental).

Uso:
    python cli.py                     # energía, modelo por defecto
    python cli.py --base ambiental    # módulo ambiental (base valhalladb)
    python cli.py --model qwen/qwen3-coder
    python cli.py --verbose           # muestra herramientas/SQL usados
Comandos dentro del chat:
    /reset   reinicia la conversación
    /sql     muestra las consultas ejecutadas en la última respuesta
    /salir   termina
"""
from __future__ import annotations

import argparse
import sys

# En Windows la consola usa cp1252; forzar UTF-8 para imprimir "→" y demás.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src import config
from src.agent import EnergyAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="Agente IA de monitoreo (energía / ambiental)")
    parser.add_argument("--model", default=config.DEFAULT_MODEL, help="Modelo de OpenRouter")
    parser.add_argument("--base", default=config.DEFAULT_BASE,
                        choices=list(config.DBS), help="Módulo/base de datos")
    parser.add_argument("--verbose", "-v", action="store_true", help="Mostrar herramientas y SQL")
    args = parser.parse_args()

    cfg = config.get_db_config(args.base)
    print(f"Agente de {cfg.label} (base {cfg.dbname}@{cfg.host}:{cfg.port}) — modelo: {args.model}")
    print("Escribe tu pregunta. /reset reinicia, /sql muestra consultas, /salir termina.\n")

    agent = EnergyAgent(model=args.model, verbose=args.verbose, base=args.base)
    last_result = None

    while True:
        try:
            question = input("tú> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n¡Hasta luego!")
            break
        if not question:
            continue
        if question == "/salir":
            print("¡Hasta luego!")
            break
        if question == "/reset":
            agent.reset()
            print("(conversación reiniciada)\n")
            continue
        if question == "/sql":
            if last_result and last_result.queries:
                print("\n--- SQL ejecutado ---")
                for q in last_result.queries:
                    print(q + "\n")
            else:
                print("(sin consultas en la última respuesta)\n")
            continue

        result = agent.ask(question)
        last_result = result
        if result.error and result.error != "max_iterations":
            print(f"\n[error] {result.error}\n")
        print(f"\nagente> {result.answer}\n")
        if args.verbose:
            u = result.usage
            print(
                f"  [iteraciones={result.iterations} "
                f"tokens_in={u.get('prompt_tokens', 0)} tokens_out={u.get('completion_tokens', 0)}]\n"
            )


if __name__ == "__main__":
    main()
