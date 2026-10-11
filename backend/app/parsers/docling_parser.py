"""
parsers/docling_parser.py
--------------------------
Shared Docling document parser with embedded image enrichment.

Handles all document types through a single unified function:
  parse_with_docling(file_bytes, filename, fmt)

  fmt values and the file types they cover:
    "pdf"   → .pdf  (digital pages + scanned OCR pages)
    "docx"  → .docx / .doc
    "pptx"  → .pptx
    "html"  → .html / .htm
    "md"    → .md

Called directly from:
  - agents/evidence_collector.py  (_DOCLING_FMT dispatch map)
  - parsers/zip_extractor.py      (_DOCLING_FMT dispatch map)

Why Docling?
  - Preserves document structure: headings, paragraphs, lists, tables
  - Maintains reading order even in multi-column PDFs
  - Built-in OCR backend for scanned PDFs
  - Exports to Markdown — ideal for structure-aware semantic chunking

Image Enrichment:
  Docling exports embedded images as `<!-- image -->` Markdown placeholders.
  For formats that support embedded images (PDF, DOCX, PPTX), this module:
    1. Extracts images via document_image_extractor (pypdfium2 / python-docx / python-pptx)
    2. Runs EasyOCR (visible text) + Gemini (scene description) on each image
    3. Replaces `<!-- image -->` placeholders with the extracted content
  This ensures image-heavy documents contribute meaningful content to the
  vector store for RAG retrieval.
"""

from __future__ import annotations

import logging
import re
import tempfile
import os
from typing import Literal

logger = logging.getLogger(__name__)

# Supported Docling input formats
DoclingFormat = Literal["pdf", "docx", "pptx", "html", "md"]

# Pattern matching the image placeholder Docling emits
_IMAGE_PLACEHOLDER = re.compile(r"<!-- image -->")
_PAGE_BREAK = "<!-- page break -->"


def _with_page_markers(markdown_text: str) -> str:
    """Replace Docling page breaks with numbered "[Page N]" lines (single-page documents get none)."""
    pages = markdown_text.split(_PAGE_BREAK)
    if len(pages) == 1:
        return markdown_text
    numbered = (f"[Page {number}]\n\n{page.strip()}" for number, page in enumerate(pages, start=1) if page.strip())
    return "\n\n".join(numbered)


def parse_with_docling(
    file_bytes: bytes,
    filename: str,
    fmt: DoclingFormat,
) -> str:
    """
    Parse a document with Docling and return its content as structured Markdown.

    Docling preserves:
      - Heading hierarchy (# H1, ## H2, etc.)
      - Paragraphs and line breaks
      - Bullet and numbered lists
      - Table content (as Markdown tables)
      - Reading order (including multi-column layouts in PDFs)

    For PDF, DOCX, and PPTX files, embedded images are extracted and
    processed through EasyOCR + Gemini, replacing the `<!-- image -->`
    placeholders with actual OCR text and scene descriptions.

    For scanned PDFs Docling automatically activates its OCR backend.

    Args:
        file_bytes: Raw file bytes of the document.
        filename:   Original filename (used for the header line and temp file naming).
        fmt:        Docling format hint — one of "pdf", "docx", "pptx", "html", "md".

    Returns:
        Structured Markdown string, with "[Page N]" marker lines for multi-page
        documents (so records and quotes can cite their page). Returns "" when the
        document has no extractable content.

    Raises:
        ValueError    for an unsupported format
        RuntimeError  when Docling cannot parse the file (the collector reports it)
    """
    try:
        from docling.document_converter import DocumentConverter
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import PdfFormatOption
    except ImportError as exc:
        raise ImportError(
            "docling is not installed. Run: pip install docling"
        ) from exc

    # ── Map fmt string → Docling InputFormat ─────────────────────────────────
    _FMT_MAP: dict[str, InputFormat] = {
        "pdf":  InputFormat.PDF,
        "docx": InputFormat.DOCX,
        "pptx": InputFormat.PPTX,
        "html": InputFormat.HTML,
        "md":   InputFormat.MD,
    }

    input_format = _FMT_MAP.get(fmt)
    if input_format is None:
        raise ValueError(f"unsupported document format '{fmt}'")

    # ── Write bytes to a temp file (Docling requires a file path) ────────────
    suffix = f".{fmt}" if fmt != "md" else ".md"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        # ── Build converter with PDF OCR option if needed ─────────────────
        if fmt == "pdf":
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True          # enable OCR for scanned pages
            pipeline_options.do_table_structure = True  # detect tables
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        else:
            converter = DocumentConverter()

        # ── Convert ───────────────────────────────────────────────────────
        result = converter.convert(tmp_path)
        doc = result.document

        # ── Export to Markdown, marking page boundaries ───────────────────
        markdown_text = _with_page_markers(doc.export_to_markdown(page_break_placeholder=_PAGE_BREAK))

        if not markdown_text or not markdown_text.strip():
            return ""

        # ── Enrich image placeholders with OCR + vision content ───────────
        # Works for PDF, DOCX, PPTX — skipped for HTML/MD (no embedded images)
        markdown_text = _enrich_document_images(markdown_text, file_bytes, filename, fmt)

        return f"[Document: {filename}]\n\n{markdown_text.strip()}"

    except Exception as exc:
        raise RuntimeError(f"Docling could not parse the document: {exc}") from exc

    finally:
        # Always clean up the temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Image enrichment (unified for all formats) ──────────────────────────────

def _enrich_document_images(
    markdown_text: str,
    file_bytes: bytes,
    filename: str,
    fmt: DoclingFormat,
) -> str:
    """
    Replace `<!-- image -->` placeholders in Docling's Markdown output
    with actual image content from EasyOCR + Gemini.

    Strategy:
      1. Count `<!-- image -->` placeholders — bail early if none
      2. Extract images from the document using document_image_extractor
      3. Process each image through EasyOCR (text) + Gemini (description)
      4. Replace each placeholder sequentially with the corresponding content

    Args:
        markdown_text: Docling Markdown with `<!-- image -->` placeholders.
        file_bytes:    Raw document bytes (for image extraction).
        filename:      Original filename (for logging).
        fmt:           Document format (determines which extractor to use).

    Returns:
        Enriched Markdown with image placeholders replaced by descriptions.
        Returns original text unchanged if no placeholders or no images found.
    """
    # Count placeholders — bail early if none
    placeholder_count = len(_IMAGE_PLACEHOLDER.findall(markdown_text))
    if placeholder_count == 0:
        return markdown_text

    logger.info(
        "%s: Found %d image placeholder(s), extracting embedded images...",
        filename, placeholder_count,
    )

    # ── Extract images using the unified extractor ────────────────────────
    try:
        from app.parsers.document_image_extractor import extract_images
        extracted_images = extract_images(file_bytes, fmt)
    except Exception as exc:
        logger.warning("%s: Image extraction failed: %s", filename, exc)
        return markdown_text  # Return original — don't break the pipeline

    if not extracted_images:
        logger.info("%s: No embedded images found in document", filename)
        return markdown_text

    logger.info(
        "%s: Processing %d image(s) with selective OCR/vision...",
        filename, len(extracted_images),
    )

    # ── Process each image through EasyOCR + Gemini ──────────────────────
    image_descriptions: list[str] = []
    for idx, img in enumerate(extracted_images):
        desc = _describe_image(
            img.image_bytes,
            img.label,
            idx,
            document_text=markdown_text,
        )
        image_descriptions.append(desc)

    # ── Replace placeholders sequentially ─────────────────────────────────
    desc_iter = iter(image_descriptions)

    def _replace_match(match: re.Match) -> str:
        desc = next(desc_iter, None)
        if desc:
            return desc
        return ""

    enriched = _IMAGE_PLACEHOLDER.sub(_replace_match, markdown_text)

    # ── Append extra images not matched to placeholders ───────────────────
    remaining = list(desc_iter)
    if remaining:
        logger.info(
            "%s: %d extra image(s) appended (more images than placeholders)",
            filename, len(remaining),
        )
        enriched += "\n\n" + "\n\n".join(remaining)

    return enriched


def _describe_image(
    image_bytes: bytes,
    label: str,
    index: int,
    *,
    document_text: str = "",
) -> str:
    """
    Generate useful content for a single embedded image.

    EasyOCR runs first. If OCR mostly duplicates text Docling already extracted
    from the document, the image is skipped. Gemini is called only for visual
    images where OCR alone is not enough.

    Args:
        image_bytes: Raw image bytes.
        label:       Human-readable label (e.g. "Slide 2 - Picture 3").
        index:       Image index (for logging).

    Returns:
        Formatted string with OCR text and/or image description.
    """
    parts: list[str] = []

    # ── EasyOCR — extract visible text ────────────────────────────────────
    try:
        from app.services.ocr_service import extract_text_from_image_bytes
        ocr_text = extract_text_from_image_bytes(image_bytes).strip()
    except Exception as exc:
        logger.warning("OCR failed for image %d (%s): %s", index, label, exc)
        ocr_text = ""

    # ── Gemini — scene description ───────────────────────────────────────
    if ocr_text and _is_already_in_document(ocr_text, document_text):
        logger.info("Skipping %s: OCR text already captured by Docling", label)
        return ""

    ocr_word_count = len(_tokens(ocr_text))
    text_heavy = len(ocr_text) >= 80 or ocr_word_count >= 12
    should_describe = not text_heavy and _image_is_large_enough_for_caption(image_bytes)

    # ── Build combined output ─────────────────────────────────────────────
    if ocr_text:
        parts.append(f"[Image OCR Text]\n{ocr_text}")

    if should_describe:
        try:
            from app.services.caption_service import describe_image
            description = describe_image(image_bytes, f"{label}.png").strip()
        except Exception as exc:
            logger.warning("Caption failed for image %d (%s): %s", index, label, exc)
            description = ""

        if description and not description.startswith("["):
            # Skip error messages like "[Caption service unavailable: ...]"
            parts.append(f"[Image Description]\n{description}")
    elif ocr_text:
        logger.info("Skipping Gemini for %s: OCR text is sufficient", label)

    if not parts:
        return ""

    return "\n" + "\n\n".join(parts) + "\n"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]{3,}", text.lower())


def _is_already_in_document(ocr_text: str, document_text: str) -> bool:
    """Return True when Docling already captured most OCR words."""
    ocr_tokens = _tokens(ocr_text)
    if len(ocr_tokens) < 8:
        return False

    document_tokens = set(_tokens(document_text))
    if not document_tokens:
        return False

    matched = sum(1 for token in ocr_tokens if token in document_tokens)
    return matched / len(ocr_tokens) >= 0.65


def _image_is_large_enough_for_caption(image_bytes: bytes) -> bool:
    """Avoid Gemini calls for icons, logos, decorative fills, and tiny images."""
    if len(image_bytes) < 30_000:
        return False

    try:
        import io
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        width, height = image.size
        return width >= 300 and height >= 200
    except Exception:
        return True
