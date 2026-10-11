"""
parsers/json_parser.py
----------------------
Parse JSON evidence into searchable, record-structured text.

  • A list of objects (e.g. chat messages, audit events, transactions) — found at
    the top level or under a wrapper key such as "messages"/"events" — is rendered
    one record per line:  [Message 1] Time: … | From: … | To: … | Message: …
  • Chat-shaped records merge sender/recipient name + ID ("Name (ID)") so no
    participant information is lost.
  • Scalar metadata (e.g. case_reference) is kept as "Key: value" lines.
  • Any other JSON is flattened to "path: value" lines.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.parsers import decode_text, format_record, parse_warning

_MAX_ITEMS = 2000
_MAX_VALUE_CHARS = 500
_ID_VALUE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")

_TIME_KEYS = ("timestamp", "datetime", "time", "date", "sent_at", "created_at")
_TEXT_KEYS = ("message", "text", "content", "body", "msg")
_SENDER_PREFIXES = ("sender", "from", "author", "user")
_RECIPIENT_PREFIXES = ("recipient", "receiver", "to")


def parse(file_bytes: bytes, filename: str = "evidence.json") -> str:
    text = decode_text(file_bytes)
    header = f"[JSON Evidence: {filename}]"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        warning = parse_warning(f"not valid JSON (line {exc.lineno}, column {exc.colno}); read as plain text")
        return f"{header}\n{warning}\n\n{text}" if text.strip() else ""

    lines = [header, ""]
    records, metadata = _split_records(data)
    if not records:
        flat = _flatten(data)
        if len(flat) >= _MAX_ITEMS:
            lines.append(parse_warning(f"only the first {_MAX_ITEMS} values were read"))
        lines.extend(flat)
        return "\n".join(lines).strip()

    lines.extend(f"{_pretty_key(key)}: {_scalar(value)}" for key, value in metadata.items())
    is_chat = _looks_like_chat(records)
    label = "Message" if is_chat else "Record"
    lines += ["", f"Total records: {len(records)}", ""]
    if len(records) > _MAX_ITEMS:
        lines.append(parse_warning(f"only the first {_MAX_ITEMS} of {len(records)} records were read"))
    for index, record in enumerate(records[:_MAX_ITEMS], start=1):
        fields = _chat_fields(record) if is_chat else _record_fields(record)
        lines.append(format_record(label, index, fields))
    return "\n".join(lines).strip()


# ── Structure detection ───────────────────────────────────────────────────────

def _split_records(data: Any) -> tuple[list[dict], dict]:
    """Return (record list, scalar metadata) for list-of-object shaped JSON."""
    if _is_record_list(data):
        return data, {}
    if not isinstance(data, dict):
        return [], {}
    lists = [value for value in data.values() if _is_record_list(value)]
    if not lists:
        return [], {}
    metadata = {k: v for k, v in data.items() if not isinstance(v, (list, dict)) and v not in (None, "")}
    return max(lists, key=len), metadata


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _looks_like_chat(records: list[dict]) -> bool:
    keys = {key.lower() for record in records[:20] for key in record}
    has_text = any(key in keys for key in _TEXT_KEYS)
    has_sender = any(key.startswith(_SENDER_PREFIXES) for key in keys)
    return has_text and has_sender


# ── Field rendering ───────────────────────────────────────────────────────────

def _record_fields(record: dict) -> list[tuple[str, str]]:
    return [(_pretty_key(key), _scalar(value)) for key, value in record.items()]


def _chat_fields(record: dict) -> list[tuple[str, str]]:
    """Order chat fields as Time / From / To / Channel / Message, then the rest."""
    remaining = dict(record)
    time_value = _pop_first(remaining, _TIME_KEYS)
    sender = _pop_participant(remaining, _SENDER_PREFIXES)
    recipient = _pop_participant(remaining, _RECIPIENT_PREFIXES)
    channel = _pop_first(remaining, ("channel", "room", "thread"))
    message = _pop_first(remaining, _TEXT_KEYS)
    fields = [("Time", time_value), ("From", sender), ("To", recipient), ("Channel", channel), ("Message", message)]
    return fields + _record_fields(remaining)


def _pop_first(record: dict, keys: tuple[str, ...]) -> str:
    for key in list(record):
        if key.lower() in keys:
            return _scalar(record.pop(key))
    return ""


def _pop_participant(record: dict, prefixes: tuple[str, ...]) -> str:
    """Combine every sender_*/recipient_* style field into 'Name (ID)'."""
    names: list[str] = []
    ids: list[str] = []
    for key in list(record):
        lowered = key.lower()
        if not any(lowered == p or lowered.startswith(p + "_") for p in prefixes):
            continue
        value = _scalar(record.pop(key))
        target = ids if _ID_VALUE.match(value) else names
        if value and value not in target:
            target.append(value)
    label = ", ".join(names) or ", ".join(ids)
    return f"{label} ({', '.join(ids)})" if names and ids else label


def _pretty_key(key: str) -> str:
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(key)).replace("_", " ").split()
    return " ".join(word.upper() if word.lower() in {"id", "ip", "po"} else word.capitalize() for word in words)


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        text = ", ".join(_scalar(item) for item in value if _scalar(item))
    elif isinstance(value, dict):
        text = "; ".join(f"{_pretty_key(k)}={_scalar(v)}" for k, v in value.items())
    else:
        text = str(value)
    text = " ".join(text.split())
    return text[:_MAX_VALUE_CHARS].rstrip() + "..." if len(text) > _MAX_VALUE_CHARS else text


def _flatten(value: Any, path: str = "", out: list[str] | None = None) -> list[str]:
    out = [] if out is None else out
    if len(out) >= _MAX_ITEMS:
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(item, f"{path}.{key}" if path else str(key), out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flatten(item, f"{path}[{index}]", out)
    elif _scalar(value):
        out.append(f"{path or 'value'}: {_scalar(value)}")
    return out
