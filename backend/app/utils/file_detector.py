"""
utils/file_detector.py
-----------------------
Granular file type detection for the Evidence Collector Agent.

Returns a specific type string used by the parser dispatcher in
agents/evidence_collector.py:

  Type string   → Parser
  ─────────────────────────────────────────────────────────
  "pdf"         → parsers/pdf_parser.py       (Docling)
  "docx"        → parsers/docx_parser.py      (Docling)
  "doc"         → parsers/docx_parser.py      (Docling, legacy)
  "pptx"        → parsers/pptx_parser.py      (Docling)
  "html"        → parsers/html_parser.py      (Docling)
  "markdown"    → parsers/md_parser.py        (Docling)
  "image"       → parsers/image_parser.py     (EasyOCR + Gemini)
  "email"       → parsers/email_parser.py     (Python stdlib email)
  "msg"         → parsers/msg_parser.py       (extract-msg)
  "csv"         → parsers/csv_parser.py       (Python stdlib csv)
  "excel"       → parsers/excel_parser.py     (pandas + openpyxl)
  "xml"         → parsers/xml_parser.py       (lxml / ElementTree)
  "chat"        → parsers/chat_parser.py      (custom)
  "zip"         → parsers/zip_extractor.py    (zipfile + recursive dispatch)
  "txt"         → plain text decode (utf-8 / latin-1 fallback)
  "audio"       → audio_service (transcription — handled separately)
  "video"       → video metadata — handled separately
  "unknown"     → unsupported
"""

from pathlib import Path


# Extension → type mapping
_EXT_MAP: dict[str, str] = {
    # ── Document types (Docling) ──────────────────────────────────────────────
    ".pdf":  "pdf",
    ".docx": "docx",
    ".doc":  "doc",
    ".pptx": "pptx",
    ".html": "html",
    ".htm":  "html",
    ".md":   "markdown",
    # ── Plain text ────────────────────────────────────────────────────────────
    ".txt":  "txt",
    # ── Tabular data ─────────────────────────────────────────────────────────
    ".csv":  "csv",
    ".xlsx": "excel",
    ".xls":  "excel",
    # ── Structured data ───────────────────────────────────────────────────────
    ".xml":  "xml",
    ".json": "json",    # Parsed as chat when chat-shaped, otherwise generic JSON
    # ── Images (EasyOCR + Gemini) ────────────────────────────────────────────
    ".jpg":  "image",
    ".jpeg": "image",
    ".png":  "image",
    ".webp": "image",
    ".gif":  "image",
    ".heic": "image",
    ".bmp":  "image",
    ".tiff": "image",
    ".tif":  "image",
    # ── Email ─────────────────────────────────────────────────────────────────
    ".eml":  "email",
    ".msg":  "msg",     # Outlook — uses extract-msg (separate from .eml)
    # ── Archives ─────────────────────────────────────────────────────────────
    ".zip":  "zip",
    # ── Audio ────────────────────────────────────────────────────────────────
    ".mp3":  "audio",
    ".wav":  "audio",
    ".m4a":  "audio",
    ".ogg":  "audio",
    ".flac": "audio",
    # ── Video ────────────────────────────────────────────────────────────────
    ".mp4":  "video",
    ".mov":  "video",
    ".avi":  "video",
    ".mkv":  "video",
}


def detect_file_type(filename: str) -> str:
    """
    Detect the specific evidence file type from the filename extension.

    Args:
        filename: Original filename (e.g. "crime_scene.jpg", "report.pdf").

    Returns:
        Type string: one of the keys in the mapping above, or "unknown".
    """
    ext = Path(filename).suffix.lower()
    return _EXT_MAP.get(ext, "unknown")


def is_text_based(file_type: str) -> bool:
    """Return True if the file type produces extractable text."""
    return file_type in {
        "pdf", "docx", "doc", "pptx", "html", "markdown",
        "txt", "csv", "excel", "xml", "email", "msg", "chat", "json",
    }


def is_image(file_type: str) -> bool:
    """Return True if the file type is an image."""
    return file_type == "image"


def is_media(file_type: str) -> bool:
    """Return True if the file type is audio or video."""
    return file_type in {"audio", "video"}


def is_archive(file_type: str) -> bool:
    """Return True if the file type is an archive."""
    return file_type == "zip"
