"""Loop del agente: conversación + function calling contra OpenRouter."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from openai import OpenAI

from . import config, tunnel
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SPECS, dispatch

MAX_ITERATIONS = 15


@dataclass
class AgentResult:
    answer: str
    queries: list = field(default_factory=list)  # SQL ejecutadas
    charts: list = field(default_factory=list)   # specs de gráficos
    iterations: int = 0
    usage: dict = field(default_factory=dict)
    error: str | None = None


class EnergyAgent:
    def __init__(self, model: str | None = None, verbose: bool = False):
        self.model = model or config.DEFAULT_MODEL
        self.verbose = verbose
        self.client = OpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
        )
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.queries: list[str] = []
        self.charts: list[dict] = []
        tunnel.ensure_tunnel()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.queries = []
        self.charts = []

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def ask(self, question: str) -> AgentResult:
        self.messages.append({"role": "user", "content": question})
        queries_before = len(self.queries)
        charts_before = len(self.charts)
        result = AgentResult(answer="")
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

        for i in range(1, MAX_ITERATIONS + 1):
            result.iterations = i
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=TOOL_SPECS,
                    tool_choice="auto",
                    temperature=0.1,
                )
            except Exception as e:
                result.error = f"Error llamando al modelo: {e}"
                return result

            if resp.usage:
                total_usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                total_usage["completion_tokens"] += resp.usage.completion_tokens or 0

            msg = resp.choices[0].message
            self.messages.append(msg.model_dump(exclude_none=True))

            tool_calls = msg.tool_calls or []
            if not tool_calls:
                result.answer = msg.content or ""
                break

            for tc in tool_calls:
                self._log(f"  → herramienta: {tc.function.name}({tc.function.arguments[:200]})")
                if tc.function.name == "run_query":
                    try:
                        self.queries.append(json.loads(tc.function.arguments)["sql"])
                    except Exception:
                        pass
                elif tc.function.name == "render_chart":
                    try:
                        chart = json.loads(tc.function.arguments)
                        if len(self.charts) < 3:  # tope de gráficos por respuesta
                            self.charts.append(chart)
                    except json.JSONDecodeError:
                        pass
                output = dispatch(tc.function.name, tc.function.arguments)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": output,
                    }
                )
        else:
            result.answer = (
                "No pude completar el análisis dentro del límite de pasos. "
                "Intenta reformular la pregunta o dividirla en partes."
            )
            result.error = "max_iterations"

        result.queries = self.queries[queries_before:]
        result.charts = self.charts[charts_before:]
        result.usage = total_usage
        return result
