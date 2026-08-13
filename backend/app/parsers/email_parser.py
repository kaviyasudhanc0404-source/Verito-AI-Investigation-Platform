"""
parsers/email_parser.py
------------------------
Extract structured text from email evidence files (.eml, .msg).

Uses Python's built-in `email` stdlib — no extra dependencies.

Extracted fields:
  - From, To, CC, Subject, Date
  - Full text body (plain text parts; HTML stripped to text as fallback)
  - Attachment list (filenames only, not content)
"""

import email
import email.policy
from email.message import Message
from html.parser import HTMLParser


class _HTMLStripper(HTMLParser):
    """Minimal HTML tag stripper for email bodies."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def _decode_payload(part: Message) -> str:
    """Safely decode a message part payload to a string."""
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def parse(file_bytes: bytes, filename: str = "email.eml") -> str:
    """
    Parse an .eml file and return structured plain text.

    Args:
        file_bytes: Raw .eml file bytes.
        filename: Original filename (for context).

    Returns:
        Structured plain text: headers + body + attachment list.
    """
    msg = email.message_from_bytes(file_bytes, policy=email.policy.compat32)

    # ── Headers ───────────────────────────────────────────────────────────────
    lines: list[str] = [
        f"[Email Evidence: {filename}]",
        f"From: {msg.get('From', 'Unknown')}",
        f"To: {msg.get('To', 'Unknown')}",
    ]
    if msg.get("Cc"):
        lines.append(f"CC: {msg.get('Cc')}")
    lines += [
        f"Subject: {msg.get('Subject', '(No Subject)')}",
        f"Date: {msg.get('Date', 'Unknown')}",
        "",
    ]

    # ── Body ──────────────────────────────────────────────────────────────────
    body_parts: list[str] = []
    attachments: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in disposition:
                att_name = part.get_filename() or "unnamed_attachment"
                attachments.append(att_name)
                continue

            if content_type == "text/plain":
                body_parts.append(_decode_payload(part))
            elif content_type == "text/html" and not body_parts:
                # Use HTML body only if no plain text found
                html_text = _decode_payload(part)
                body_parts.append(_strip_html(html_text))
    else:
        content_type = msg.get_content_type()
        payload_text = _decode_payload(msg)
        if content_type == "text/html":
            payload_text = _strip_html(payload_text)
        body_parts.append(payload_text)

    if body_parts:
        lines.append("EMAIL BODY:")
        lines.append("\n".join(body_parts).strip())
    else:
        lines.append("EMAIL BODY:\n(No readable body found)")

    # ── Attachments ───────────────────────────────────────────────────────────
    if attachments:
        lines.append("")
        lines.append(f"ATTACHMENTS: {', '.join(attachments)}")

    return "\n".join(lines)
