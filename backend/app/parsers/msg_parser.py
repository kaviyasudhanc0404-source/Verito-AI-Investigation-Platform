"""
parsers/msg_parser.py
----------------------
Extract structured text from Outlook message files (.msg) using extract-msg.

Extracted fields:
  - From, To, CC, Subject, Date
  - Full text body (plain text preferred; HTML stripped as fallback)
  - Attachment list (filenames only)

Separate from email_parser.py (.eml via Python stdlib).
.msg uses Microsoft's Compound Document format → requires extract-msg library.
"""

from __future__ import annotations


def parse(file_bytes: bytes, filename: str = "email.msg") -> str:
    """
    Parse an Outlook .msg file and return structured plain text.

    Args:
        file_bytes: Raw .msg file bytes.
        filename:   Original filename (for context).

    Returns:
        Structured plain text: headers + body + attachment list.
    """
    try:
        import extract_msg  # pip install extract-msg
    except ImportError as exc:
        raise ImportError(
            "extract-msg is not installed. Run: pip install extract-msg"
        ) from exc

    import tempfile, os

    # Write to a temp file — some extract-msg versions need a real file path
    suffix = ".msg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        msg = extract_msg.Message(tmp_path)
    except Exception as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return f"[MSG Evidence: {filename}]\n(Failed to parse: {exc})"

    try:
        lines: list[str] = [
            f"[Outlook MSG Evidence: {filename}]",
            f"From: {msg.sender or 'Unknown'}",
            f"To: {msg.to or 'Unknown'}",
        ]

        if msg.cc:
            lines.append(f"CC: {msg.cc}")

        # msg.date may be a datetime object in newer extract-msg — str() handles both
        lines += [
            f"Subject: {msg.subject or '(No Subject)'}",
            f"Date: {str(msg.date) if msg.date else 'Unknown'}",
            "",
        ]

        # ── Body ──────────────────────────────────────────────────────────────
        body = (msg.body or "").strip()
        if body:
            lines.append("EMAIL BODY:")
            lines.append(body)
        else:
            # Try HTML body as fallback — API differs across extract-msg versions
            html_raw = b""
            for attr in ("htmlBody", "html_body", "compressedRtf"):
                val = getattr(msg, attr, None)
                if val:
                    html_raw = val if isinstance(val, bytes) else val.encode("utf-8", errors="replace")
                    break

            if html_raw:
                from html.parser import HTMLParser

                class _Stripper(HTMLParser):
                    def __init__(self) -> None:
                        super().__init__()
                        self._parts: list[str] = []

                    def handle_data(self, data: str) -> None:
                        self._parts.append(data)

                    def get_text(self) -> str:
                        return " ".join(self._parts).strip()

                html_text = html_raw.decode("utf-8", errors="replace")
                stripper = _Stripper()
                stripper.feed(html_text)
                plain = stripper.get_text()
                if plain:
                    lines.append("EMAIL BODY:")
                    lines.append(plain)
                else:
                    lines.append("EMAIL BODY:\n(No readable body found)")
            else:
                lines.append("EMAIL BODY:\n(No readable body found)")

        # ── Attachments ───────────────────────────────────────────────────────
        if msg.attachments:
            att_names = []
            for att in msg.attachments:
                # Support both camelCase (older) and snake_case (newer) API
                name = (
                    getattr(att, "longFilename", None)
                    or getattr(att, "long_filename", None)
                    or getattr(att, "shortFilename", None)
                    or getattr(att, "short_filename", None)
                    or "unnamed_attachment"
                )
                att_names.append(name)
            lines.append("")
            lines.append(f"ATTACHMENTS: {', '.join(att_names)}")

        return "\n".join(lines)

    finally:
        try:
            msg.close()
        except Exception:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
