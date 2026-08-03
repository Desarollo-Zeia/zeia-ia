"""Compara varios modelos de OpenRouter con las mismas preguntas.

Para cada (modelo, pregunta) ejecuta el agente con conversación fresca y
registra: respuesta, SQL ejecutado, iteraciones, latencia, tokens y costo
estimado (según pricing público de OpenRouter).

Uso:
    python eval/compare_models.py                      # set por defecto
    python eval/compare_models.py --models a/b c/d
    python eval/compare_models.py --questions q1_empresas_puntos q5_alertas_criticas
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.agent import EnergyAgent

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "eval" / "results"

DEFAULT_MODELS = [
    "deepseek/deepseek-v3.2",
    "qwen/qwen3-coder",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-4.1-mini",
    "anthropic/claude-sonnet-4.5",
]


def fetch_pricing() -> dict:
    """Devuelve {model_id: (precio_prompt, precio_completion)} por token."""
    req = urllib.request.Request(
        f"{config.OPENROUTER_BASE_URL}/models",
        headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
    )
    try:
        data = json.load(urllib.request.urlopen(req, timeout=30))["data"]
        return {
            m["id"]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"]))
            for m in data
        }
    except Exception as e:
        print(f"(aviso) no se pudo obtener pricing: {e}")
        return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--questions", nargs="+", default=None, help="IDs de preguntas a correr")
    parser.add_argument("--file", default="questions.yaml", help="Archivo YAML dentro de eval/")
    args = parser.parse_args()

    questions = yaml.safe_load((ROOT / "eval" / args.file).read_text())
    if args.questions:
        questions = [q for q in questions if q["id"] in args.questions]
        if not questions:
            print("Ninguna pregunta coincide con los IDs dados.")
            sys.exit(1)

    pricing = fetch_pricing()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"run_{stamp}.json"

    results = {"timestamp": stamp, "models": args.models, "runs": []}

    for model in args.models:
        print(f"\n{'='*70}\nMODELO: {model}\n{'='*70}")
        for q in questions:
            print(f"  [{q['id']}] ", end="", flush=True)
            agent = EnergyAgent(model=model, verbose=False)
            t0 = time.time()
            r = agent.ask(q["question"])
            elapsed = time.time() - t0

            p_in, p_out = pricing.get(model, (0.0, 0.0))
            cost = (
                r.usage.get("prompt_tokens", 0) * p_in
                + r.usage.get("completion_tokens", 0) * p_out
            )
            run = {
                "model": model,
                "question_id": q["id"],
                "difficulty": q["difficulty"],
                "question": q["question"],
                "expected_facts": q["expected_facts"],
                "answer": r.answer,
                "queries": r.queries,
                "iterations": r.iterations,
                "elapsed_s": round(elapsed, 2),
                "usage": r.usage,
                "cost_usd": round(cost, 6),
                "error": r.error,
            }
            results["runs"].append(run)
            status = "ERROR" if r.error else "ok"
            print(
                f"{status} | {elapsed:5.1f}s | {r.iterations} it | "
                f"{len(r.queries)} queries | ${cost:.5f}"
            )
            # Guardar incrementalmente por si algo falla a mitad
            out_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # Resumen por modelo
    print(f"\n{'='*70}\nRESUMEN\n{'='*70}")
    print(f"{'modelo':45s} {'ok':>3} {'err':>4} {'t_med':>7} {'costo':>9}")
    for model in args.models:
        runs = [r for r in results["runs"] if r["model"] == model]
        errs = sum(1 for r in runs if r["error"])
        avg_t = sum(r["elapsed_s"] for r in runs) / max(len(runs), 1)
        total_cost = sum(r["cost_usd"] for r in runs)
        print(f"{model:45s} {len(runs)-errs:>3} {errs:>4} {avg_t:6.1f}s ${total_cost:8.5f}")

    print(f"\nResultados completos en: {out_path}")


if __name__ == "__main__":
    main()
