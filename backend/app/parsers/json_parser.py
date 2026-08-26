"""
parsers/json_parser.py
----------------------
Parse arbitrary JSON evidence into searchable text.

Chat-shaped JSON is still handled by chat_parser. Other structured JSON files
are flattened with paths so keys and values remain meaningful for retrieval.
"""

from __future__ import annotations

import json
from typing import Any

from app.parsers.chat_parser import parse as parse_chat

_MAX_ITEMS = 2000
_MAX_VALUE_CHARS = 500


def parse(file_bytes: bytes, filename: str = "evidence.json") -> str:
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1", errors="replace")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text

    chat_text = parse_chat(file_bytes, filename)
    chat_lines = [line for line in chat_text.splitlines() if line.strip()]
    if len(chat_lines) > 1:
        return chat_text

    lines = [f"[JSON Evidence: {filename}]", ""]
    lines.extend(_flatten_json(data))
    return "\n".join(lines).strip()


def _flatten_json(value: Any, path: str = "$", count: list[int] | None = None) -> list[str]:
    if count is None:
        count = [0]
    if count[0] >= _MAX_ITEMS:
        return []

    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path != "$" else str(key)
            lines.extend(_flatten_json(item, child_path, count))
            if count[0] >= _MAX_ITEMS:
                break
        return lines

    if isinstance(value, list):
        for index, item in enumerate(value):
            lines.extend(_flatten_json(item, f"{path}[{index}]", count))
            if count[0] >= _MAX_ITEMS:
                break
        return lines

    if value is None:
        return []

    text = str(value).strip()
    if not text:
        return []
    if len(text) > _MAX_VALUE_CHARS:
        text = text[:_MAX_VALUE_CHARS].rstrip() + "..."

    count[0] += 1
    return [f"{path}: {text}"]
