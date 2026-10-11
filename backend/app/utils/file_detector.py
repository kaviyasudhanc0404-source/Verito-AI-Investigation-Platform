"""
utils/file_detector.py
----------------------
Single source of truth for supported evidence file extensions.

Each extension maps to:
  type — the parser dispatch key used by app.parsers.parse_evidence()
  kind — the broad category shown by the frontend (icon/grouping)
"""

from pathlib import Path

_DOC, _TEXT, _CHAT, _IMAGE, _AUDIO, _VIDEO = "doc", "text", "chat", "image", "audio", "video"

_EXTENSIONS: dict[str, tuple[str, str]] = {
    # Documents (Docling)
    ".pdf": ("pdf", _DOC),
    ".docx": ("docx", _DOC),
    ".doc": ("doc", _DOC),
    ".pptx": ("pptx", _DOC),
    ".html": ("html", _DOC),
    ".htm": ("html", _DOC),
    ".md": ("markdown", _DOC),
    # Plain text and structured data
    ".txt": ("txt", _TEXT),
    ".csv": ("csv", _TEXT),
    ".xlsx": ("excel", _TEXT),
    ".xls": ("excel", _TEXT),
    ".xml": ("xml", _TEXT),
    ".json": ("json", _TEXT),
    # Communications
    ".eml": ("email", _CHAT),
    ".msg": ("msg", _CHAT),
    # Images (OCR + vision captioning)
    ".jpg": ("image", _IMAGE),
    ".jpeg": ("image", _IMAGE),
    ".png": ("image", _IMAGE),
    ".webp": ("image", _IMAGE),
    ".gif": ("image", _IMAGE),
    ".heic": ("image", _IMAGE),
    ".bmp": ("image", _IMAGE),
    ".tiff": ("image", _IMAGE),
    ".tif": ("image", _IMAGE),
    # Archives
    ".zip": ("zip", _DOC),
    # Audio / video
    ".mp3": ("audio", _AUDIO),
    ".wav": ("audio", _AUDIO),
    ".m4a": ("audio", _AUDIO),
    ".ogg": ("audio", _AUDIO),
    ".flac": ("audio", _AUDIO),
    ".mp4": ("video", _VIDEO),
    ".mov": ("video", _VIDEO),
    ".avi": ("video", _VIDEO),
    ".mkv": ("video", _VIDEO),
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_EXTENSIONS)


def _entry(filename: str) -> tuple[str, str]:
    return _EXTENSIONS.get(Path(filename).suffix.lower(), ("unknown", _DOC))


def detect_file_type(filename: str) -> str:
    """Return the parser dispatch key for a filename, or "unknown"."""
    return _entry(filename)[0]


def detect_kind(filename: str) -> str:
    """Return the broad evidence kind shown in the UI."""
    return _entry(filename)[1]


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS
