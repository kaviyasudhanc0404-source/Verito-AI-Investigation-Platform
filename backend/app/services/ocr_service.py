"""
services/ocr_service.py
------------------------
EasyOCR wrapper for extracting visible text from images.

Used by:
  - image_parser.py  (direct image evidence)
  - pdf_parser.py    (scanned PDF pages rendered as images)

The EasyOCR reader is lazy-loaded and cached as a module-level singleton
so it is only initialised once per process (model load ~2-5 seconds).

Supported languages: English (configurable via EASYOCR_LANGUAGES env var).
GPU is used automatically if available (CUDA), otherwise falls back to CPU.
"""

from __future__ import annotations

import os
from typing import Any

# Module-level singleton — loaded once on first use
_reader: Any | None = None

# Languages to recognise — extend as needed (e.g. ["en", "ta"] for Tamil)
_LANGUAGES: list[str] = os.environ.get("EASYOCR_LANGUAGES", "en").split(",")


def _get_reader() -> Any:
    """Lazy-load and return the EasyOCR Reader instance."""
    global _reader
    if _reader is None:
        try:
            import easyocr
        except ImportError as exc:
            raise ImportError(
                "easyocr is not installed. Run: pip install easyocr"
            ) from exc
        # gpu=True uses CUDA if available, False forces CPU
        use_gpu = os.environ.get("EASYOCR_GPU", "true").lower() not in {"false", "0", "no"}
        _reader = easyocr.Reader(_LANGUAGES, gpu=use_gpu, verbose=False)
    return _reader


def extract_text_from_image_bytes(image_bytes: bytes) -> str:
    """
    Run EasyOCR on raw image bytes and return extracted text.

    Args:
        image_bytes: Raw image bytes (PNG, JPEG, WEBP, etc.).

    Returns:
        Concatenated OCR text. Returns empty string if nothing detected.
    """
    if not image_bytes:
        return ""

    reader = _get_reader()

    # EasyOCR accepts a numpy array or raw bytes-like via readtext()
    results = reader.readtext(image_bytes, detail=0, paragraph=True)

    return "\n".join(str(r).strip() for r in results if str(r).strip())


def extract_text_from_path(image_path: str) -> str:
    """
    Run EasyOCR on an image file at the given path.

    Args:
        image_path: Absolute or relative path to the image file.

    Returns:
        Concatenated OCR text.
    """
    reader = _get_reader()
    results = reader.readtext(image_path, detail=0, paragraph=True)
    return "\n".join(str(r).strip() for r in results if str(r).strip())
