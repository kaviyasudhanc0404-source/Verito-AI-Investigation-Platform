"""
parsers/chat_parser.py
-----------------------
Extract structured text from chat log evidence files.

Supports:
  - Plain text logs (.txt) — line-by-line with common patterns:
      [HH:MM] Name: message
      Name (HH:MM): message
      Name: message
  - JSON structured chat exports (.json) — expects list of objects with
      keys like: timestamp/time/date, sender/author/from/name, message/text/content

Uses only Python's built-in `json` stdlib — no extra dependencies.
"""

import json
import re


# Common chat line patterns to recognise
_PATTERNS = [
    # [2024-01-15 10:30:45] John: Hello
    re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?P<sender>[^:]+):\s+(?P<msg>.+)$"),
    # 10:30 - John: Hello
    re.compile(r"^(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\s+-\s+(?P<sender>[^:]+):\s+(?P<msg>.+)$"),
    # John (10:30): Hello
    re.compile(r"^(?P<sender>[^(]+)\s+\((?P<ts>[^)]+)\):\s+(?P<msg>.+)$"),
    # John: Hello  (no timestamp)
    re.compile(r"^(?P<sender>[A-Za-z][^:]{0,40}):\s+(?P<msg>.+)$"),
]


def _parse_txt_chat(text: str) -> list[str]:
    """Parse a plain-text chat log into formatted lines."""
    results: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        matched = False
        for pattern in _PATTERNS:
            m = pattern.match(line)
            if m:
                groups = m.groupdict()
                ts = groups.get("ts", "").strip()
                sender = groups.get("sender", "").strip()
                msg = groups.get("msg", "").strip()
                if ts:
                    results.append(f"[{ts}] {sender}: {msg}")
                else:
                    results.append(f"{sender}: {msg}")
                matched = True
                break

        if not matched:
            # Keep unrecognised lines as-is (system messages, etc.)
            results.append(line)

    return results


def _parse_json_chat(data: list | dict) -> list[str]:
    """Parse a JSON structured chat export into formatted lines."""
    messages: list = []
    if isinstance(data, dict):
        # Common wrappers: {"messages": [...]} or {"chats": [...]}
        for key in ("messages", "chats", "data", "conversation"):
            if key in data and isinstance(data[key], list):
                messages = data[key]
                break
        if not messages:
            messages = [data]
    elif isinstance(data, list):
        messages = data

    results: list[str] = []
    for entry in messages:
        if not isinstance(entry, dict):
            continue

        # Find timestamp
        ts = ""
        for key in ("timestamp", "time", "date", "datetime", "created_at", "sent_at"):
            if key in entry:
                ts = str(entry[key])
                break

        # Find sender
        sender = ""
        for key in ("sender", "author", "from", "name", "user", "username"):
            if key in entry:
                sender = str(entry[key])
                break

        # Find message text
        msg = ""
        for key in ("message", "text", "content", "body", "msg"):
            if key in entry:
                msg = str(entry[key])
                break

        if not msg:
            continue

        if ts and sender:
            results.append(f"[{ts}] {sender}: {msg}")
        elif sender:
            results.append(f"{sender}: {msg}")
        else:
            results.append(msg)

    return results


def parse(file_bytes: bytes, filename: str = "chat.txt") -> str:
    """
    Parse a chat log file (plain text or JSON) into structured evidence text.

    Args:
        file_bytes: Raw file bytes.
        filename: Original filename (used to detect JSON vs. plain text).

    Returns:
        Formatted plain text with one message per line.
    """
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else "txt"

    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1", errors="replace")

    lines: list[str] = [f"[Chat Log Evidence: {filename}]", ""]

    if suffix == "json":
        try:
            data = json.loads(text)
            parsed = _parse_json_chat(data)
        except json.JSONDecodeError:
            # Fall back to plain text if JSON parsing fails
            parsed = _parse_txt_chat(text)
    else:
        parsed = _parse_txt_chat(text)

    lines.extend(parsed)
    return "\n".join(lines).strip()
