"""
parsers/email_parser.py
------------------------
Extract structured text from .eml evidence (Python stdlib only).

One .eml file may hold several messages (an mbox export or messages pasted one
after another). Each message is split out and rendered as:

    [Email 2] Date: 2026-03-07T14:34:27+05:30 | From: … | To: … | Subject: …
    <plain-text body>
    Attachments: a.pdf, b.png

Encoded headers (=?utf-8?…?=), quoted-printable/base64 bodies and HTML-only
bodies are decoded to readable text.
"""

from __future__ import annotations

import email
import email.policy
import re
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from app.parsers import decode_text, format_record

_HEADER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")
_KEY_HEADERS = ("from:", "to:", "date:", "subject:")
_MBOX_SEPARATOR = re.compile(r"^From \S+.*\d{4}\s*$")


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def parse(file_bytes: bytes, filename: str = "email.eml") -> str:
    messages = [email.message_from_string(raw, policy=email.policy.default) for raw in _split_messages(decode_text(file_bytes))]
    lines = [f"[Email Evidence: {filename}]", f"Messages: {len(messages)}", ""]
    for index, message in enumerate(messages, start=1):
        lines.extend(_render(index, message))
        lines.append("")
    return "\n".join(lines).strip()


def _split_messages(text: str) -> list[str]:
    """Split raw text wherever a new header block (or mbox separator) begins."""
    lines = text.replace("\r\n", "\n").split("\n")
    starts = [0]
    for i in range(1, len(lines)):
        if _MBOX_SEPARATOR.match(lines[i]) or (not lines[i - 1].strip() and _starts_header_block(lines, i)):
            starts.append(i)
    segments = ["\n".join(lines[a:b]) for a, b in zip(starts, starts[1:] + [len(lines)])]
    cleaned = [re.sub(r"^From \S+.*\n", "", seg).strip() for seg in segments]
    return [seg for seg in cleaned if seg] or [text]


def _starts_header_block(lines: list[str], start: int) -> bool:
    block: list[str] = []
    for line in lines[start:start + 30]:
        if not line.strip():
            break
        if not (_HEADER_LINE.match(line) or line[:1] in " \t"):
            return False
        block.append(line.lower())
    found = sum(any(line.startswith(key) for line in block) for key in _KEY_HEADERS)
    return found >= 3


def _render(index: int, message: EmailMessage) -> list[str]:
    headers = [
        ("Date", _iso_date(message.get("Date", ""))),
        ("From", str(message.get("From", ""))),
        ("To", str(message.get("To", ""))),
        ("CC", str(message.get("Cc", ""))),
        ("Subject", " ".join(str(message.get("Subject", "")).split())),
    ]
    body, attachments = _body_and_attachments(message)
    lines = [format_record("Email", index, headers), body or "(No readable body)"]
    if attachments:
        lines.append(f"Attachments: {', '.join(attachments)}")
    return lines


def _body_and_attachments(message: EmailMessage) -> tuple[str, list[str]]:
    attachments = [part.get_filename() or "unnamed" for part in message.iter_attachments()]
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return "", attachments
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        content = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        stripper = _HTMLStripper()
        stripper.feed(content)
        content = " ".join(stripper.parts)
    return content.strip(), attachments


def _iso_date(value: str) -> str:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        return value.strip()
