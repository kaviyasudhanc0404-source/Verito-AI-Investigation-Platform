"""
services/caption_service.py
---------------------------
Gemini image captioning / scene description service.

Images are sent to the Gemini API for a concise forensic scene description.
Visible text extraction remains the responsibility of EasyOCR; this service
focuses on visual context such as people, objects, setting, actions, and
spatial relationships.
"""

from __future__ import annotations

import base64
import io
import mimetypes

from app.core.config import get_settings

settings = get_settings()

_SYSTEM_PROMPT = (
    "You are a forensic image analyst. Describe this image in detail for an "
    "investigation report. Include: the environment and setting, all visible "
    "people and their actions, vehicles with visible identifiers, objects of "
    "interest, notable spatial relationships, and visual context. Be precise "
    "and factual. Do not speculate beyond what is clearly visible. Do not "
    "perform OCR; visible text is extracted separately by EasyOCR."
)

_SUPPORTED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


def _prepare_image_for_gemini(image_bytes: bytes, filename: str) -> tuple[str, bytes]:
    """
    Return a Gemini-supported MIME type and image bytes.

    Large images are resized before upload to reduce latency and payload size.
    Unsupported/unknown image formats are converted to JPEG.
    """
    guessed_mime = mimetypes.guess_type(filename)[0]
    max_dim = max(256, int(settings.gemini_max_image_dimension))

    try:
        from PIL import Image
    except ImportError:
        if guessed_mime in _SUPPORTED_MIME_TYPES:
            return guessed_mime, image_bytes
        return "image/jpeg", image_bytes

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.thumbnail((max_dim, max_dim))

        has_alpha = image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        )
        output = io.BytesIO()

        if has_alpha:
            image.save(output, format="PNG", optimize=True)
            return "image/png", output.getvalue()

        image = image.convert("RGB")
        image.save(output, format="JPEG", quality=85, optimize=True)
        return "image/jpeg", output.getvalue()
    except Exception:
        if guessed_mime in _SUPPORTED_MIME_TYPES:
            return guessed_mime, image_bytes
        return "image/jpeg", image_bytes


def _extract_text_from_response(payload: dict) -> str:
    """Extract concatenated text parts from a Gemini generateContent response."""
    texts: list[str] = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                texts.append(str(text).strip())
    return "\n".join(t for t in texts if t).strip()


def describe_image(image_bytes: bytes, filename: str = "image.jpg") -> str:
    """
    Generate a forensic scene description for an image using Gemini.

    Args:
        image_bytes: Raw image bytes.
        filename: Original filename, used for MIME hints and diagnostics.

    Returns:
        Natural language description of the image content.
        Returns a bracketed error string if the API call cannot complete.
    """
    if not image_bytes:
        return ""

    if not settings.gemini_api_key:
        return "[Caption service unavailable: GEMINI_API_KEY is not configured]"

    try:
        import httpx
    except ImportError as exc:
        return f"[Caption service unavailable: {exc}]"

    try:
        mime_type, prepared_bytes = _prepare_image_for_gemini(image_bytes, filename)
        encoded = base64.b64encode(prepared_bytes).decode("ascii")

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent"
        )
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": _SYSTEM_PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": encoded,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 256,
            },
        }

        response = httpx.post(
            url,
            headers={
                "x-goog-api-key": settings.gemini_api_key,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=settings.gemini_timeout_seconds,
        )
        response.raise_for_status()

        description = _extract_text_from_response(response.json())
        return description or "[Image description failed: empty Gemini response]"
    except Exception as exc:
        return f"[Image description failed: {exc}]"
