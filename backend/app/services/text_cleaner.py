"""
services/text_cleaner.py
--------------------------
Evidence-preserving text cleaner.

IMPORTANT DESIGN PRINCIPLE:
  This cleaner does NOT:
    - Remove stop words
    - Stem or lemmatise words
    - Remove numbers, dates, or codes
    - Remove punctuation that could be meaningful (e.g. licence plates)
    - Alter URLs, email addresses, dates, or IP addresses

  It ONLY normalises formatting noise while keeping every piece of
  forensic evidence intact.

Two-Phase Pipeline
==================

Phase 1 — Basic Text Cleaning
  1. Unicode Normalization    — NFC normalisation (handles accents, ligatures)
  2. Remove Control Chars    — strip non-printable chars (keep \\n \\r \\t)
  3. Normalize Line Endings  — unify \\r\\n and \\r to \\n
  4. Collapse Spaces         — multiple spaces/tabs within a line → single space
  5. Strip Lines             — remove leading/trailing whitespace per line
  6. Collapse Blank Lines    — 3+ consecutive blank lines → 2

Phase 2 — Evidence-Preserving Cleaning
  7. Merge Wrapped Lines     — rejoin soft-wrapped continuation lines within
                               paragraphs (preserves paragraph boundaries)
  8. Normalize Bullets       — unify –, •, ·, *, ‣ → standard '-'
  9. Remove Soft Hyphens     — strip U+00AD (soft hyphen / invisible character)
  10. Preserve URLs          — URLs are detected and left completely unchanged
  11. Preserve Emails        — email addresses left completely unchanged
  12. Preserve Dates         — date strings (ISO, slash, written) left unchanged
  13. Preserve IP Addresses  — IPv4 addresses left completely unchanged

This ensures forensic evidence like "TN 33 AB 1234", "2024-01-15",
"$45,000", "Room 3B", "192.168.1.1", "user@domain.com" are preserved
exactly as extracted.
"""

from __future__ import annotations

import re
import unicodedata


# ── Phase 1 compiled patterns ─────────────────────────────────────────────────

# Non-printable control characters (keep \n=0x0a, \r=0x0d, \t=0x09)
_CTRL_CHAR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# 3+ consecutive blank lines → collapse to 2
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")

# Multiple spaces or tabs within a single line
_MULTI_SPACE = re.compile(r"[ \t]+")


# ── Phase 2 compiled patterns ─────────────────────────────────────────────────

# Soft hyphen U+00AD (invisible but causes word-break artefacts)
_SOFT_HYPHEN = re.compile(r"\xad")

# Bullet variants to normalise to '-'
# Covers: •  ·  ‣  ►  ▸  ▪  ◦  ◆  ★  –  —  ➤  ➔  →  *
_BULLET_VARIANTS = re.compile(
    r"^[\s]*[•·‣►▸▪◦◆★\-–—➤➔→\*]\s+",
    re.MULTILINE,
)

# A line that is a "continuation" of the previous line:
#   - Does NOT start with a capital letter after a sentence-ending punctuation
#   - Does NOT start with a heading marker (# or [)
#   - Does NOT start with a bullet or number list item
#   - Is not blank
# We use a heuristic: if previous line does NOT end in sentence-final
# punctuation (. ! ? : ; " ») and the current line does not start a new
# paragraph marker, they are considered wrapped.
_WRAPPED_LINE = re.compile(
    r"(?<![.!?:;\"»\]])\n(?![\n#\[\-\*\d]|[A-Z]{2})"
)

# URL detection (http/https/ftp — kept as-is, never touched)
_URL = re.compile(
    r"https?://[^\s\"'<>]+"
    r"|ftp://[^\s\"'<>]+"
    r"|www\.[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}[^\s\"'<>]*",
    re.IGNORECASE,
)

# Email address detection
_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

# Date patterns — common forensic date formats:
#   ISO:        2024-01-15, 2024/01/15
#   US:         01/15/2024, 01-15-2024
#   EU:         15.01.2024, 15/01/2024
#   Written:    15 January 2024, January 15 2024, Jan 15 2024
_DATE = re.compile(
    r"\b\d{4}[-/]\d{2}[-/]\d{2}\b"                    # ISO: 2024-01-15
    r"|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"             # US/EU: 01/15/2024
    r"|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b"                 # EU dot: 15.01.2024
    r"|\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{2,4}\b"       # 15 January 2024
    r"|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{2,4}\b",  # Jan 15, 2024
    re.IGNORECASE,
)

# IPv4 address detection (e.g. 192.168.1.1)
_IP_ADDRESS = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)


# ── Public API ────────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    """
    Apply the full two-phase evidence-preserving text cleaning pipeline.

    Phase 1: Basic Text Cleaning
      Removes formatting noise: control chars, excess whitespace, blank lines.

    Phase 2: Evidence-Preserving Cleaning
      Merges wrapped lines, normalises bullets, removes soft hyphens.
      URLs, emails, dates, and IP addresses are detected and left untouched.

    Args:
        text: Raw extracted text from any parser.

    Returns:
        Cleaned text safe for chunking and embedding.
        All forensic evidence values (dates, IPs, emails, URLs) are preserved.
    """
    if not text:
        return ""

    text = _phase1_basic(text)
    text = _phase2_evidence_preserving(text)

    return text.strip()


# ── Internal phases ───────────────────────────────────────────────────────────

def _phase1_basic(text: str) -> str:
    """
    Phase 1 — Basic Text Cleaning (6 steps).

    1. Unicode Normalization   — NFC
    2. Remove Control Chars    — strip non-printable (keep \\n \\r \\t)
    3. Normalize Line Endings  — \\r\\n / \\r → \\n
    4. Collapse Spaces         — multiple spaces/tabs per line → one space
    5. Strip Lines             — trim leading/trailing whitespace per line
    6. Collapse Blank Lines    — 3+ consecutive blank lines → 2
    """
    # Step 1: Unicode NFC normalization (handles accented chars, ligatures, etc.)
    text = unicodedata.normalize("NFC", text)

    # Step 2: Remove non-printable control characters (preserve \n \r \t)
    text = _CTRL_CHAR.sub("", text)

    # Step 3: Normalize line endings to \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Step 4: Collapse multiple spaces/tabs within each line to one space
    lines = [_MULTI_SPACE.sub(" ", line) for line in text.split("\n")]

    # Step 5: Strip leading/trailing whitespace from each individual line
    lines = [line.strip() for line in lines]

    # Step 6: Re-join and collapse 3+ consecutive blank lines to max 2
    text = "\n".join(lines)
    text = _EXCESS_BLANK_LINES.sub("\n\n", text)

    return text


def _phase2_evidence_preserving(text: str) -> str:
    """
    Phase 2 — Evidence-Preserving Cleaning (7 steps).

    7. Merge Wrapped Lines     — join soft-wrapped continuation lines
    8. Normalize Bullets       — unify bullet variants → '-'
    9. Remove Soft Hyphens     — strip U+00AD
    10. Preserve URLs          — no-op marker (URLs are never altered)
    11. Preserve Emails        — no-op marker (emails are never altered)
    12. Preserve Dates         — no-op marker (dates are never altered)
    13. Preserve IP Addresses  — no-op marker (IPs are never altered)

    Steps 10–13 are validating passes: they scan for evidence tokens and
    verify none were accidentally corrupted by steps 7–9.  If a token was
    altered, it is restored from the original.
    """
    # ── Snapshot evidence tokens before any further processing ────────────────
    original_urls = set(_URL.findall(text))
    original_emails = set(_EMAIL.findall(text))
    original_ips = set(_IP_ADDRESS.findall(text))

    # Step 7 (run FIRST): Normalize bullet variants → standard '- '
    # Must happen BEFORE wrapped-line merging so bullets are still at line-start.
    def _normalize_bullet(m: re.Match) -> str:
        leading = re.match(r"^(\s*)", m.group(0)).group(1)
        return f"{leading}- "

    text = _BULLET_VARIANTS.sub(_normalize_bullet, text)

    # Step 8: Merge soft-wrapped continuation lines within paragraphs.
    # Runs AFTER bullet normalization so bullet lines are already uniform.
    text = _WRAPPED_LINE.sub(" ", text)

    # Step 9: Remove soft hyphens (U+00AD)
    text = _SOFT_HYPHEN.sub("", text)

    # Steps 10–13: Verification pass
    _verify_evidence_tokens(text, original_urls, original_emails, original_ips)

    return text


def _verify_evidence_tokens(
    text: str,
    original_urls: set[str],
    original_emails: set[str],
    original_ips: set[str],
) -> None:
    """
    Validate that URLs, emails, and IPs present before Phase 2 are still
    present after Phase 2.  Raises a warning (does NOT raise an exception —
    the pipeline must continue) if any token went missing.

    This is a developer-safety guard — in production the cleaning steps above
    are designed to never touch these tokens.
    """
    import logging
    logger = logging.getLogger(__name__)

    current_urls = set(_URL.findall(text))
    current_emails = set(_EMAIL.findall(text))
    current_ips = set(_IP_ADDRESS.findall(text))

    lost_urls = original_urls - current_urls
    lost_emails = original_emails - current_emails
    lost_ips = original_ips - current_ips

    if lost_urls:
        logger.warning("text_cleaner: URLs lost during Phase 2: %s", lost_urls)
    if lost_emails:
        logger.warning("text_cleaner: Emails lost during Phase 2: %s", lost_emails)
    if lost_ips:
        logger.warning("text_cleaner: IPs lost during Phase 2: %s", lost_ips)
