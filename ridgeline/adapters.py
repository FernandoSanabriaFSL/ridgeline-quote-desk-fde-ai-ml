"""Source adapters: turn one RFQ file into plain text for the extraction model.

Dispatch is by file suffix only. There are no per-customer or per-layout templates.
Files that can't be read as text (images, scanned PDFs, unknown types) are reported as
unsupported; stage 4 turns those into review lines.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".eml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".heic"}


@dataclass(frozen=True)
class SourceDoc:
    path: Path
    status: str  # "ok" or "unsupported"
    text: str = ""
    reason: str | None = None


def _one_line(value: object) -> str:
    """A cell as one line of text: None is empty, whitespace (including newlines) is collapsed."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _read_text(path: Path) -> SourceDoc:
    return SourceDoc(path, "ok", path.read_text(encoding="utf-8", errors="replace"))


def _read_pdf(path: Path) -> SourceDoc:
    import pdfplumber

    sections: list[str] = []
    has_text = False
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            has_text = has_text or bool(text)
            table_blocks = []
            for index, table in enumerate(page.extract_tables(), start=1):
                rows = [" | ".join(_one_line(cell) for cell in row) for row in table]
                table_blocks.append(f"(table {index})\n" + "\n".join(rows))
            sections.append(
                f"=== PAGE {number} ===\n"
                f"PAGE TEXT:\n{text}\n\n"
                f"TABLES:\n{chr(10).join(table_blocks) if table_blocks else '(none)'}"
            )
    if not has_text:
        return SourceDoc(path, "unsupported", reason="needs OCR / unsupported: PDF has no text layer")
    return SourceDoc(path, "ok", "\n\n".join(sections))


def _read_xlsx(path: Path) -> SourceDoc:
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True)
    sections: list[str] = []
    for sheet in workbook.worksheets:
        if sheet.sheet_state != "visible":
            continue
        rows = []
        for row in sheet.iter_rows():
            cells = [_one_line(cell.value) for cell in row]
            if any(cells):
                rows.append(f"R{row[0].row}: " + " | ".join(cells).rstrip())
        sections.append(f"=== SHEET {sheet.title} ===\n" + "\n".join(rows))
    return SourceDoc(path, "ok", "\n\n".join(sections))


def read_source(path: Path) -> SourceDoc:
    """Read one RFQ file. Raises on a corrupt file; the caller turns that into a failure."""
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _read_text(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".xlsx":
        return _read_xlsx(path)
    kind = "image" if suffix in IMAGE_SUFFIXES else "file type"
    return SourceDoc(path, "unsupported", reason=f"needs OCR / unsupported: {kind} ({suffix or 'no extension'})")
