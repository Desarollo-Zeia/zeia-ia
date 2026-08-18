"""Herramientas para leer documentos PDF del proyecto (facturas, reportes).

Motor principal: pdf-inspector (Firecrawl) → Markdown estructurado con tablas y
orden de lectura correcto. Fallback: pypdf (texto plano).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config

PDF_DIR = config.ROOT  # PDFs en la raíz del proyecto

MAX_EXTRACT_CHARS = 18000


def _safe_path(filename: str) -> Path | None:
    """Valida que filename sea un PDF existente en la raíz del proyecto."""
    if not filename or filename != Path(filename).name:
        return None
    path = PDF_DIR / filename
    if path.suffix.lower() != ".pdf" or not path.is_file():
        return None
    return path


def _page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def list_documents() -> dict:
    """Lista los PDFs disponibles en la raíz del proyecto."""
    docs = []
    for path in sorted(PDF_DIR.glob("*.pdf")):
        docs.append({
            "filename": path.name,
            "size_kb": round(path.stat().st_size / 1024),
            "pages": _page_count(path),
        })
    if not docs:
        return {"error": "No hay PDFs en la raíz del proyecto"}
    return {"documents": docs}


def extract_pdf_text(
    filename: str,
    page_from: int | None = None,
    page_to: int | None = None,
) -> dict:
    """Extrae el texto/Markdown de un PDF del proyecto (páginas 1-indexed)."""
    path = _safe_path(filename)
    if path is None:
        return {"error": f"PDF no encontrado en la raíz del proyecto: {filename!r}"}

    pages: list[int] | None = None
    if page_from is not None or page_to is not None:
        total = _page_count(path)
        start = max(1, page_from or 1)
        end = min(total, page_to or start)
        pages = list(range(start, end + 1))

    try:
        import pdf_inspector  # motor preferido (Markdown + tablas)

        result = pdf_inspector.process_pdf(str(path), pages=pages)
        text = result.markdown or ""
        pdf_type = result.pdf_type
        pages_with_tables = result.pages_with_tables
    except Exception:
        # Fallback: pypdf en texto plano
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        idx = range(0, len(reader.pages))
        if pages:
            idx = [p - 1 for p in pages if 1 <= p <= len(reader.pages)]
        text = "\n\n".join(
            f"<!-- página {i + 1} -->\n" + (reader.pages[i].extract_text() or "")
            for i in idx
        )
        pdf_type = "text_based"
        pages_with_tables = []

    truncated = len(text) > MAX_EXTRACT_CHARS
    if truncated:
        text = text[:MAX_EXTRACT_CHARS] + "\n... [texto truncado]"

    return {
        "filename": path.name,
        "pdf_type": pdf_type,
        "pages": pages or None,
        "pages_with_tables": pages_with_tables,
        "truncated": truncated,
        "markdown": text,
    }
