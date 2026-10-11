"""
parsers/document_image_extractor.py
-------------------------------------
Unified image extractor for all Docling-supported document formats.

Extracts embedded images from:
  PPTX  → python-pptx  (slide shapes, grouped shapes, placeholders)
  DOCX  → python-docx  (inline images from document relationships)
  PDF   → pypdfium2 (embedded images per page; installed with Docling)
  HTML  → not applicable (external URLs, not embedded binary data)
  MD    → not applicable (external URLs, not embedded binary data)

Built-in optimisations:
  - Hash-based deduplication: same image on multiple slides → extracted once
  - Size filter: tiny images (< 5KB) are skipped (logos, bullets, icons)

Returns a flat list of ExtractedImage named tuples that the
docling_parser feeds into EasyOCR for text enrichment.

Called from:
  - parsers/docling_parser.py  (_enrich_document_images)
"""

from __future__ import annotations

import hashlib
import io
import logging
from typing import Literal, NamedTuple

logger = logging.getLogger(__name__)

DoclingFormat = Literal["pdf", "docx", "pptx", "html", "md"]

# Images smaller than this are skipped (logos, bullets, decorative icons)
_MIN_IMAGE_BYTES = 5120  # 5 KB


class ExtractedImage(NamedTuple):
    """One embedded image extracted from a document."""
    image_bytes: bytes      # raw image data (JPEG, PNG, etc.)
    content_type: str       # MIME type, e.g. "image/jpeg"
    label: str              # human-readable label for logging


# ── Public API ────────────────────────────────────────────────────────────────

def extract_images(file_bytes: bytes, fmt: DoclingFormat) -> list[ExtractedImage]:
    """
    Extract all embedded images from a document.

    Dispatches to the correct extraction logic based on the document format.
    HTML and Markdown are skipped (images are external references, not embedded).
    Images are deduplicated by content hash and filtered by minimum size.

    Args:
        file_bytes: Raw file bytes of the document.
        fmt:        Document format — one of "pdf", "docx", "pptx", "html", "md".

    Returns:
        List of unique ExtractedImage tuples (deduplicated, size-filtered).
        Empty list if no images found or format not supported.
    """
    _EXTRACTORS = {
        "pptx": _extract_from_pptx,
        "docx": _extract_from_docx,
        "pdf":  _extract_from_pdf,
    }

    extractor = _EXTRACTORS.get(fmt)
    if extractor is None:
        return []

    try:
        raw_images = extractor(file_bytes)
    except Exception as exc:
        logger.warning("Image extraction failed for %s: %s", fmt.upper(), exc)
        return []

    # ── Deduplicate + size filter ─────────────────────────────────────────
    unique_images = _deduplicate(raw_images)

    logger.info(
        "%s: %d raw image(s) → %d unique image(s) after dedup + size filter",
        fmt.upper(), len(raw_images), len(unique_images),
    )
    return unique_images


def _deduplicate(images: list[ExtractedImage]) -> list[ExtractedImage]:
    """Remove duplicate images (by content hash) and images below minimum size."""
    seen_hashes: set[str] = set()
    unique: list[ExtractedImage] = []

    for img in images:
        # Skip tiny images
        if len(img.image_bytes) < _MIN_IMAGE_BYTES:
            continue

        # Skip duplicates (same image bytes on multiple slides/pages)
        img_hash = hashlib.md5(img.image_bytes).hexdigest()
        if img_hash in seen_hashes:
            continue
        seen_hashes.add(img_hash)

        unique.append(img)

    return unique


# ── PPTX extraction (python-pptx) ────────────────────────────────────────────

def _extract_from_pptx(file_bytes: bytes) -> list[ExtractedImage]:
    """Extract images from a PowerPoint file using python-pptx."""
    from pptx import Presentation

    prs = Presentation(io.BytesIO(file_bytes))
    images: list[ExtractedImage] = []

    for slide_idx, slide in enumerate(prs.slides, start=1):
        _collect_pptx_shapes(slide.shapes, slide_idx, images)

    return images


def _collect_pptx_shapes(
    shapes,
    slide_num: int,
    images: list[ExtractedImage],
) -> None:
    """Recursively collect images from PPTX shapes (handles groups)."""
    from pptx.shapes.picture import Picture

    for shape in shapes:
        # Direct picture shape
        if isinstance(shape, Picture):
            try:
                img = shape.image
                images.append(ExtractedImage(
                    image_bytes=img.blob,
                    content_type=img.content_type,
                    label=f"Slide {slide_num} - {shape.name}",
                ))
            except Exception as exc:
                logger.debug("PPTX: skip shape %s: %s", shape.name, exc)

        # Grouped shapes — recurse
        elif hasattr(shape, "shapes"):
            try:
                _collect_pptx_shapes(shape.shapes, slide_num, images)
            except Exception:
                pass

        # Auto-shape or placeholder with image fill
        elif hasattr(shape, "image"):
            try:
                img = shape.image
                images.append(ExtractedImage(
                    image_bytes=img.blob,
                    content_type=img.content_type,
                    label=f"Slide {slide_num} - {shape.name}",
                ))
            except Exception:
                pass


# ── DOCX extraction (python-docx) ────────────────────────────────────────────

def _extract_from_docx(file_bytes: bytes) -> list[ExtractedImage]:
    """Extract images from a Word document using python-docx."""
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    images: list[ExtractedImage] = []

    img_count = 0
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                img_part = rel.target_part
                img_count += 1
                images.append(ExtractedImage(
                    image_bytes=img_part.blob,
                    content_type=img_part.content_type,
                    label=f"DOCX Image {img_count}",
                ))
            except Exception as exc:
                logger.debug("DOCX: skip relationship %s: %s", rel.rId, exc)

    return images


# ── PDF extraction (pypdfium2, installed with Docling) ───────────────────────

def _extract_from_pdf(file_bytes: bytes) -> list[ExtractedImage]:
    """Extract embedded images from a PDF with pypdfium2, as PNG bytes."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw

    pdf = pdfium.PdfDocument(file_bytes)
    images: list[ExtractedImage] = []
    try:
        for page_number, page in enumerate(pdf, start=1):
            for index, obj in enumerate(page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE], max_depth=3)):
                try:
                    buffer = io.BytesIO()
                    obj.get_bitmap(render=False).to_pil().save(buffer, format="PNG")
                    images.append(ExtractedImage(
                        image_bytes=buffer.getvalue(),
                        content_type="image/png",
                        label=f"Page {page_number} - Image {index + 1}",
                    ))
                except Exception as exc:
                    logger.debug("PDF: skip image %d on page %d: %s", index + 1, page_number, exc)
    finally:
        pdf.close()
    return images
