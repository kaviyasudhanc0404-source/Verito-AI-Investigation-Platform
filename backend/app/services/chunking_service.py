"""
services/chunking_service.py
-----------------------------
Structure-aware chunker for cleaned evidence text.

Chunks must stay under the embedding model's input limit (bge-small reads ~512
tokens), so MAX_CHUNK_CHARS is a hard cap. Structure is never broken:

  • Sections start at Markdown headings, [Label] marker lines and ALL-CAPS titles;
    each chunk carries its section heading for context.
  • A record line ([Row 3] …, [Record 2] …, [Message 1] …) is one atomic unit.
  • An email record line keeps its body with it; long bodies repeat the header.
  • A Markdown table is split by rows, and every piece repeats the table header.
  • Prose splits on paragraphs, then sentences, then lines, then hard length.
  • Fragments shorter than SMALL_CHUNK_CHARS (e.g. a lone heading) merge into a
    neighbour instead of becoming their own chunk. Nothing is ever dropped for
    being short — a one-line file still becomes one chunk.
"""

from __future__ import annotations

import re

MAX_CHUNK_CHARS = 1200
SMALL_CHUNK_CHARS = 300

_HEADING = re.compile(r"^#{1,6}\s+\S")
_LABEL_MARKER = re.compile(r"^\[[^\]]+\]$")
_ALLCAPS_TITLE = re.compile(r"^[A-Z][A-Z0-9\s\-/&,.]{4,80}$")
_RECORD_LINE = re.compile(r"^\[(?:Row|Record|Message|Event) \d+\]\s")
_EMAIL_LINE = re.compile(r"^\[Email \d+\]\s")
_TABLE_LINE = re.compile(r"^\|.*\|$")
_TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split cleaned text into embedding-safe, structure-preserving chunks."""
    if not text or not text.strip():
        return []
    chunks: list[str] = []
    for heading, body in _sections(text):
        chunks.extend(_pack(heading, _units(body, max_chars - len(heading) - 2), max_chars))
    chunks = _merge_small(chunks, max_chars)
    return [chunk for chunk in chunks if chunk.strip()]


# ── Sections ──────────────────────────────────────────────────────────────────

def _is_boundary(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or _LABEL_MARKER.match(line)
        or (_ALLCAPS_TITLE.match(line) and len(line.split()) >= 2)
    )


def _sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.split("\n"):
        stripped = line.strip()
        if _is_boundary(stripped):
            sections.append((stripped, []))
        else:
            sections[-1][1].append(stripped)
    return [(heading, body) for heading, body in sections if heading or any(body)]


# ── Units (indivisible pieces of a section) ───────────────────────────────────

def _units(lines: list[str], limit: int) -> list[str]:
    units: list[str] = []
    paragraph: list[str] = []
    i = 0

    def flush_paragraph() -> None:
        if paragraph:
            units.extend(_split_prose(" ".join(paragraph) if _is_prose(paragraph) else "\n".join(paragraph), limit))
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        if _RECORD_LINE.match(line):
            flush_paragraph()
            units.extend(_split_long(line, limit, " | "))
        elif _EMAIL_LINE.match(line):
            flush_paragraph()
            j = i + 1
            while j < len(lines) and not (_EMAIL_LINE.match(lines[j]) or _RECORD_LINE.match(lines[j])):
                j += 1
            units.extend(_email_units(line, lines[i + 1:j], limit))
            i = j
            continue
        elif _TABLE_LINE.match(line):
            flush_paragraph()
            j = i
            while j < len(lines) and _TABLE_LINE.match(lines[j]):
                j += 1
            units.extend(_table_units(lines[i:j], limit))
            i = j
            continue
        elif not line:
            flush_paragraph()
        else:
            paragraph.append(line)
        i += 1
    flush_paragraph()
    return units


def _is_prose(lines: list[str]) -> bool:
    """Short label lines ("From: …") stay on their own lines; prose is re-flowed."""
    return not all(re.match(r"^[A-Za-z][\w ./()-]{0,40}:\s", line) for line in lines)


def _email_units(header: str, body: list[str], limit: int) -> list[str]:
    text = "\n".join(line for line in body if line).strip()
    if len(header) + len(text) + 1 <= limit:
        return [f"{header}\n{text}".strip()]
    pieces = _split_prose(" ".join(text.split()), limit - len(header) - 1)
    return [f"{header}\n{piece}" for piece in pieces]


def _table_units(rows: list[str], limit: int) -> list[str]:
    has_header = len(rows) > 1 and _TABLE_SEPARATOR.match(rows[1])
    header = rows[:2] if has_header else []
    body = rows[2:] if has_header else rows
    header_text = "\n".join(header)
    units: list[str] = []
    current: list[str] = []
    for row in body:
        candidate = "\n".join([header_text, *current, row]).strip()
        if current and len(candidate) > limit:
            units.append("\n".join([header_text, *current]).strip())
            current = []
        current.extend(_split_long(row, limit - len(header_text) - 1, " | "))
    if current or header:
        units.append("\n".join([header_text, *current]).strip())
    return units


def _split_prose(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        for part in _split_long(sentence, limit, "\n"):
            if pieces and len(pieces[-1]) + len(part) + 1 <= limit:
                pieces[-1] = f"{pieces[-1]} {part}"
            else:
                pieces.append(part)
    return pieces


def _split_long(text: str, limit: int, separator: str) -> list[str]:
    """Split on `separator`, then hard-wrap anything still longer than `limit`."""
    limit = max(limit, 200)
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    for part in text.split(separator):
        while len(part) > limit:
            cut = part.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            pieces.append(part[:cut].strip())
            part = part[cut:].strip()
        if pieces and len(pieces[-1]) + len(separator) + len(part) <= limit:
            pieces[-1] = f"{pieces[-1]}{separator}{part}"
        elif part:
            pieces.append(part)
    return pieces


# ── Packing ───────────────────────────────────────────────────────────────────

def _pack(heading: str, units: list[str], max_chars: int) -> list[str]:
    if not units:
        return [heading] if heading else []
    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        candidate = _join(heading, [*current, unit])
        if current and len(candidate) > max_chars:
            chunks.append(_join(heading, current))
            current = []
        current.append(unit)
    chunks.append(_join(heading, current))
    return chunks


def _join(heading: str, units: list[str]) -> str:
    body = "\n".join(units)
    return f"{heading}\n{body}".strip() if heading else body.strip()


def _merge_small(chunks: list[str], max_chars: int) -> list[str]:
    merged: list[str] = []
    for chunk in chunks:
        if merged and (len(merged[-1]) < SMALL_CHUNK_CHARS or len(chunk) < SMALL_CHUNK_CHARS) \
                and len(merged[-1]) + len(chunk) + 2 <= max_chars:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        else:
            merged.append(chunk)
    return merged
