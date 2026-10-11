"""
services/text_cleaner.py
--------------------------
Evidence-preserving text cleaner.

It only removes formatting noise. It never removes stop words, numbers, codes,
punctuation, URLs, emails, dates or IP addresses.

Steps:
  1. Unicode NFC, HTML entities (&amp; → &), Markdown escapes (\\_ → _)
  2. Drop control characters and soft hyphens, unify line endings
  3. Collapse runs of spaces, trim lines, drop signature underline rows,
     shorten padded Markdown table separators (|-------| → |---|)
  4. Normalise bullet glyphs (•, ▪, – …) to "- "
  5. Re-join soft-wrapped prose lines — but never join structural lines
     (table rows, record lines, headings, list items, [Label] markers)
  6. Collapse 3+ blank lines to one blank line
"""

from __future__ import annotations

import html
import re
import unicodedata

_CTRL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\xad]")
_MARKDOWN_ESCAPE = re.compile(r"\\([_*#\[\]()`>~|-])")
_MULTI_SPACE = re.compile(r"[ \t]+")
_UNDERLINE_RUN = re.compile(r"_{4,}")
_BULLET = re.compile(r"^[•·‣►▸▪◦◆★➤➔→*–—]\s+")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
_DASH_RUN = re.compile(r"-{3,}")
_SENTENCE_END = (".", "!", "?", ":", ";", '"', "»", ")", "]", "|")
_STRUCTURAL_START = re.compile(r"^(?:#|\[|\||-\s|\d+[.)]\s|[A-Z][A-Za-z ]{0,30}:\s)")


def clean(text: str) -> str:
    """Return cleaned text with every evidence token preserved."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", html.unescape(text))
    text = _MARKDOWN_ESCAPE.sub(r"\1", text)
    text = _CTRL_CHARS.sub("", text).replace("\r\n", "\n").replace("\r", "\n")

    lines: list[str] = []
    for raw in text.split("\n"):
        line = _MULTI_SPACE.sub(" ", _UNDERLINE_RUN.sub("", raw)).strip()
        line = _BULLET.sub("- ", line)
        if _TABLE_SEPARATOR.match(line):
            line = _DASH_RUN.sub("---", line).replace(" ", "")
        if line and lines and _is_wrapped_continuation(lines[-1], line):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)

    return _EXCESS_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _is_wrapped_continuation(previous: str, line: str) -> bool:
    """True when `line` continues a prose sentence that was soft-wrapped."""
    if not previous or previous.endswith(_SENTENCE_END) or previous.endswith(","):
        return False
    if _STRUCTURAL_START.match(previous) or _STRUCTURAL_START.match(line):
        return False
    return line[0].islower() or line[0].isdigit()
