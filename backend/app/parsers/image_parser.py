"""
parsers/image_parser.py
------------------------
Parse image evidence files by combining:
  1. EasyOCR  — extracts visible text (licence plates, signage, documents)
  2. Gemini 2.5 Flash — generates a natural-language scene description

Pipeline:
  Image
    │
    ├──► EasyOCR ──────────► OCR Text
    │
    └──► Gemini Vision API ─► Scene Description
                │
                ▼
      Combined Evidence Text:
        OCR TEXT:
        <visible text>

        IMAGE DESCRIPTION:
        <AI scene description>
"""



def parse(file_bytes: bytes, filename: str = "image.jpg") -> str:
    """
    Extract both visible text (OCR) and semantic description (vision LLM)
    from an image file.

    Args:
        file_bytes: Raw image bytes (JPEG, PNG, WEBP, etc.).
        filename: Original filename (for context in output).

    Returns:
        Combined evidence text string with OCR and image description sections.
    """
    from app.services.ocr_service import extract_text_from_image_bytes
    from app.services.caption_service import describe_image

    parts: list[str] = [f"[Image Evidence: {filename}]"]

    # ── 1. OCR — extract visible text ─────────────────────────────────────────
    ocr_text = extract_text_from_image_bytes(file_bytes).strip()
    if ocr_text:
        parts.append(f"OCR TEXT:\n{ocr_text}")
    else:
        parts.append("OCR TEXT:\n(No visible text detected)")

    # ── 2. Vision LLM — scene description ─────────────────────────────────────
    description = describe_image(file_bytes, filename).strip()
    if description:
        parts.append(f"IMAGE DESCRIPTION:\n{description}")
    else:
        parts.append("IMAGE DESCRIPTION:\n(No description generated)")

    return "\n\n".join(parts)
