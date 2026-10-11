"""
app/parsers
-----------
Evidence parsers. `parse_evidence()` is the single dispatch used by the Evidence
Collector; `parse_bytes()` is reused by the ZIP extractor for archive members.

Every parser returns plain text. Structured sources (CSV, Excel, JSON, email)
render one record per line in the shared format read by app.services.records:

    [Row 3] Field: value | Field: value

Parsers are imported lazily so heavy dependencies (Docling, EasyOCR) only load
when a file of that type is actually processed.
"""

from __future__ import annotations

import asyncio
import importlib
import re
from collections.abc import Iterable
from pathlib import Path

from app.utils.file_detector import detect_file_type

_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T]00:00:00$")

_DOCLING_FORMATS = {
    "pdf": "pdf",
    "docx": "docx",
    "doc": "docx",
    "pptx": "pptx",
    "html": "html",
    "markdown": "md",
}

_MODULE_PARSERS = {
    "image": "image_parser",
    "email": "email_parser",
    "msg": "msg_parser",
    "csv": "csv_parser",
    "excel": "excel_parser",
    "xml": "xml_parser",
    "json": "json_parser",
    "zip": "zip_extractor",
}


# Problems a parser hit but worked around (invalid JSON read as text, records capped, archive
# members skipped …). Written into the text as one line each; the collector reports them.
PARSE_WARNING = re.compile(r"^\[Parse warning: (?P<text>.+)\]$", re.MULTILINE)


def parse_warning(text: str) -> str:
    return f"[Parse warning: {text}]"


def decode_text(file_bytes: bytes) -> str:
    """Decode text bytes as UTF-8 (with or without BOM), falling back to Latin-1."""
    try:
        return file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return file_bytes.decode("latin-1", errors="replace")


def clean_cell(value: object) -> str:
    """Normalise one field value for a record line (single line, no '|', no fake midnight)."""
    text = " ".join(str(value if value is not None else "").split())
    if text.lower() in {"nan", "none", "nat", "null"}:
        return ""
    return _MIDNIGHT.sub(r"\1", text).replace("|", "/")


def format_record(label: str, index: int, fields: Iterable[tuple[str, object]]) -> str:
    """Render one record in the shared line format: [Label N] Key: value | Key: value."""
    pairs = [(key, clean_cell(value)) for key, value in fields]
    return f"[{label} {index}] " + " | ".join(f"{key}: {value}" for key, value in pairs if value)


def render_table(header: list[str], rows: list[list[object]]) -> list[str]:
    """Render a header + data rows as Columns / Total rows / [Row N] lines."""
    lines = [f"Columns: {' | '.join(header)}", f"Total rows: {len(rows)}", ""]
    for number, row in enumerate(rows, start=1):
        line = format_record("Row", number, zip(header, row))
        if line.rstrip() != f"[Row {number}]":
            lines.append(line)
    return lines


def parse_bytes(file_bytes: bytes, filename: str, file_type: str | None = None) -> str:
    """Parse in-memory evidence. Audio/video need a file on disk — see parse_evidence()."""
    file_type = file_type or detect_file_type(filename)
    if file_type in _DOCLING_FORMATS:
        from app.parsers.docling_parser import parse_with_docling

        return parse_with_docling(file_bytes, filename, fmt=_DOCLING_FORMATS[file_type])
    if file_type in _MODULE_PARSERS:
        module = importlib.import_module(f"app.parsers.{_MODULE_PARSERS[file_type]}")
        return module.parse(file_bytes, filename)
    if file_type == "txt":
        return decode_text(file_bytes)
    return ""


async def parse_evidence(file_path: str, filename: str) -> str:
    """Parse a stored evidence file into text (blocking parsers run in a thread)."""
    file_type = detect_file_type(filename)
    if file_type in {"audio", "video"}:
        return await _transcribe(file_path, filename, file_type)
    file_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
    return await asyncio.to_thread(parse_bytes, file_bytes, filename, file_type)


async def _transcribe(file_path: str, filename: str, file_type: str) -> str:
    """Whisper transcript with a header line. Failures propagate so the file is reported as failed."""
    from app.services.llm_service import transcribe_audio

    return f"[{file_type.title()} Transcript: {filename}]\n\n{await transcribe_audio(file_path)}"
