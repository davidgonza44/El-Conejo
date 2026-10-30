#!/usr/bin/env python3
"""Extrae las cuatro tesis de referencia a texto plano.

Escribe REFERENCIAS-TESIS/_texto/<nombre>.txt con marca --- p. N ---.
La siguiente lectura de estilo usa esos txt; el PDF o el Word queda para el formato.

Uso: python scripts/extract_tg_texto.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pymupdf
from lxml import etree
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "REFERENCIAS-TESIS" / "_texto"
SOURCES = (
    "REFERENCIAS-TESIS/02-tg-referencia/01-predictivo-inventario/2025-UJAP-Plasencia-Valdes-Sistema-Predictivo-Inventario-ML.pdf",
    "REFERENCIAS-TESIS/02-tg-referencia/02-inteligencia-artificial/2025-UJAP-Odriozola-Asistencia-Web-Inteligencia-Artificial.pdf",
    "REFERENCIAS-TESIS/02-tg-referencia/03-estructura-desarrollo/2025-UJAP-Lopez-Africano-Control-Acceso-Inscripciones.pdf",
    "REFERENCIAS-TESIS/02-tg-referencia/04-otras-referencias/TG-DAmbrosio-Rodriguez-Chatbot-2025.docx",
)
_CEDULA = re.compile(
    r"(?i)\bC\.?\s*I\.?\s*[:.\-]?\s*(?:\d{1,2}(?:\.\d{3}){2}|\d{6,9})\b"
)


def redact(text: str) -> str:
    return _CEDULA.sub("C.I. [omitida]", text)


def header(relative: str) -> str:
    return "\n".join(
        (
            f"# {Path(relative).name}",
            f"# Origen: {relative}",
            "# Regenerar: python scripts/extract_tg_texto.py",
            "# --- p. N --- es la hoja del archivo. Para citar, usa el número impreso al pie de esa hoja.",
            "# Cédulas reemplazadas por C.I. [omitida]. No editar a mano.",
            "",
        )
    )


def pdf_body(path: Path) -> str:
    document = pymupdf.open(path)
    parts: list[str] = []
    for index, page in enumerate(document, start=1):
        parts.append(f"--- p. {index} ---")
        parts.append(page.get_text("text").strip())
        parts.append("")
    document.close()
    return "\n".join(parts)


def _page_breaks(element) -> int:
    xml = etree.tostring(element, encoding=str)
    rendered = xml.count("lastRenderedPageBreak")
    manual = xml.count('w:type="page"')
    if rendered and manual:
        return max(rendered, manual)
    return rendered + manual


def docx_body(path: Path) -> str:
    # ponytail: el número de página sale de los saltos que Word guardó;
    # si el archivo no los trae, todo queda en p. 1. Reabrir el docx solo para citar página.
    document = Document(path)
    page = 1
    lines = [f"--- p. {page} ---"]
    for child in document.element.body.iterchildren():
        breaks = _page_breaks(child)
        if breaks:
            page += breaks
            lines.extend(("", f"--- p. {page} ---"))
        if child.tag == qn("w:p"):
            text = Paragraph(child, document).text.strip()
            if text:
                lines.append(text)
        elif child.tag == qn("w:tbl"):
            for row in Table(child, document).rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                row_text = " | ".join(cell for cell in cells if cell)
                if row_text:
                    lines.append(row_text)
    return "\n".join(lines) + "\n"


def extract(relative: str) -> Path:
    source = ROOT / relative
    if source.suffix.lower() == ".pdf":
        body = pdf_body(source)
    elif source.suffix.lower() == ".docx":
        body = docx_body(source)
    else:
        raise SystemExit(f"formato no soportado: {relative}")
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{source.stem}.txt"
    target.write_text(redact(header(relative) + body), encoding="utf-8")
    return target


def check(paths: list[Path]) -> None:
    cedula = re.compile(r"(?i)\bC\.?\s*I\.?\s*[:.\-]?\s*\d")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "Planteamiento del Problema" not in text and "Planteamiento del problema" not in text:
            raise SystemExit(f"sin planteamiento: {path.name}")
        if cedula.search(text):
            raise SystemExit(f"quedó una cédula: {path.name}")
        sample = text[text.lower().find("planteamiento") : text.lower().find("planteamiento") + 500]
        long_words = sum(1 for token in sample.split() if len(token) >= 4)
        if long_words < 8:
            raise SystemExit(f"el texto salió partido por letras: {path.name}")
        if path.name.startswith("2025-UJAP-") and "--- p. 1 ---" not in text:
            raise SystemExit(f"sin marcas de página: {path.name}")


def main() -> None:
    written = [extract(relative) for relative in SOURCES]
    check(written)
    for path in written:
        print(f"{path.relative_to(ROOT).as_posix()} {path.stat().st_size}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
