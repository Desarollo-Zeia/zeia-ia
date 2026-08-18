"""Herramientas SQL que el agente puede invocar (function calling)."""
from __future__ import annotations

import json

from . import db
from . import pdf_tools

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": (
                "Lista los documentos PDF disponibles en la raíz del proyecto "
                "(p. ej. facturas mensuales de energía: 'enero-2025.pdf', "
                "'febrero-2025.pdf', ...). Devuelve nombre, tamaño y páginas."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_pdf_text",
            "description": (
                "Extrae el texto de un PDF del proyecto como Markdown "
                "estructurado (incluye tablas). Úsala tras list_documents para "
                "leer facturas/reportes y compararlos entre sí."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Nombre del PDF en la raíz del proyecto, p. ej. 'enero-2025.pdf'",
                    },
                    "page_from": {
                        "type": "integer",
                        "description": "Página inicial (1-indexed, opcional)",
                    },
                    "page_to": {
                        "type": "integer",
                        "description": "Página final inclusive (1-indexed, opcional)",
                    },
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_schemas",
            "description": "Lista los esquemas disponibles en la base de datos.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "Lista las tablas y vistas de un esquema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "schema": {"type": "string", "description": "Nombre del esquema, p. ej. 'public'"}
                },
                "required": ["schema"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "Devuelve columnas, tipos, claves primarias y foráneas de una tabla.",
            "parameters": {
                "type": "object",
                "properties": {
                    "schema": {"type": "string"},
                    "table": {"type": "string"},
                },
                "required": ["schema", "table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_query",
            "description": (
                "Ejecuta una consulta SQL de SOLO LECTURA (SELECT/WITH) contra la base de datos "
                "y devuelve columnas y filas (máx ~200). Recuerda citar con comillas dobles las "
                "columnas case-sensitive como \"P_value\"."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "Consulta SELECT en SQL PostgreSQL"}
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_chart",
            "description": (
                "Genera un gráfico para el usuario a partir de datos ya consultados con run_query. "
                "Usa 'line'/'area' para series temporales, 'bar' para rankings/comparaciones y "
                "'pie' para composiciones porcentuales. Máximo 2-3 gráficos por respuesta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["line", "bar", "area", "pie"],
                    },
                    "title": {"type": "string", "description": "Título del gráfico (en español)"},
                    "x": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Etiquetas del eje X (fechas, horas, nombres). No aplica en pie.",
                    },
                    "series": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "data": {"type": "array", "items": {"type": ["number", "null"]}},
                            },
                            "required": ["name", "data"],
                        },
                        "description": "Series numéricas; en 'pie' una sola serie con un valor por categoría.",
                    },
                    "y_unit": {"type": "string", "description": "Unidad del eje Y: kWh, kW, V, A, S/, %"},
                },
                "required": ["chart_type", "title", "series"],
            },
        },
    },
]

MAX_TOOL_RESULT_CHARS = 12000


def dispatch(name: str, arguments_json: str) -> str:
    """Ejecuta la herramienta pedida y devuelve el resultado como string JSON."""
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return json.dumps({"error": "Argumentos JSON inválidos"})

    try:
        if name == "list_documents":
            result = pdf_tools.list_documents()
        elif name == "extract_pdf_text":
            result = pdf_tools.extract_pdf_text(
                args["filename"],
                args.get("page_from"),
                args.get("page_to"),
            )
        elif name == "list_schemas":
            result = db.list_schemas()
        elif name == "list_tables":
            result = db.list_tables(args["schema"])
        elif name == "describe_table":
            result = db.describe_table(args["schema"], args["table"])
        elif name == "run_query":
            result = db.run_query(args["sql"])
        elif name == "render_chart":
            # El gráfico no se ejecuta aquí: el caller (agente/web) lo captura
            # de los argumentos de la tool call.
            # El mensaje de vuelta guía al modelo a no olvidar el texto.
            result = {
                "ok": (
                    "gráfico registrado. El gráfico COMPLEMENTA tu respuesta, "
                    "no la reemplaza: redacta ahora tu respuesta final COMPLETA "
                    "con las cifras en texto (tabla/lista) y cierra con la "
                    "sección '💡 Para tener en cuenta'."
                )
            }
        else:
            result = {"error": f"Herramienta desconocida: {name}"}
    except db.UnsafeQueryError as e:
        result = {"error": f"Consulta rechazada por seguridad: {e}"}
    except KeyError as e:
        result = {"error": f"Falta el argumento {e}"}
    except Exception as e:
        # Errores de SQL (sintaxis, columnas inexistentes...) se devuelven al
        # modelo para que los corrija.
        result = {"error": f"{type(e).__name__}: {e}"}

    out = json.dumps(result, ensure_ascii=False, default=str)
    if len(out) > MAX_TOOL_RESULT_CHARS:
        out = out[:MAX_TOOL_RESULT_CHARS] + "... [resultado truncado]"
    return out
