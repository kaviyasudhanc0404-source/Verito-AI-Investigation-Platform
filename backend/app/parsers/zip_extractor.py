"""
parsers/zip_extractor.py
-------------------------
Extract and parse evidence from ZIP archive files (.zip).

Strategy:
  1. Open the ZIP archive in memory (no disk write)
  2. List all contained files
  3. For each file inside the ZIP:
     a. Detect its type using file_detector
     b. Dispatch to the appropriate parser
     c. Collect the extracted text with a file-header label
  4. Return all content concatenated

Nested ZIPs are supported (up to 2 levels deep) to avoid infinite recursion.

Files that cannot be parsed (unsupported types, binary blobs, audio/video)
are listed but their content is skipped.
"""

from __future__ import annotations

import zipfile
import io

_MAX_DEPTH = 2  # Maximum ZIP nesting depth

# Docling handles all document types through one function
_DOCLING_FMT: dict[str, str] = {
    "pdf":      "pdf",
    "docx":     "docx",
    "doc":      "docx",
    "pptx":     "pptx",
    "html":     "html",
    "markdown": "md",
}


def _parse_inner_file(
    file_bytes: bytes,
    inner_filename: str,
    depth: int = 0,
) -> str:
    """
    Detect and parse a single file extracted from a ZIP.

    Args:
        file_bytes:      Raw bytes of the extracted file.
        inner_filename:  Filename inside the ZIP (used for type detection).
        depth:           Current ZIP nesting depth (prevents infinite recursion).

    Returns:
        Extracted text string or a skip notice.
    """
    from app.utils.file_detector import detect_file_type

    file_type = detect_file_type(inner_filename)

    # Docling handles PDF / DOCX / PPTX / HTML / Markdown in one call
    if file_type in _DOCLING_FMT:
        from app.parsers.docling_parser import parse_with_docling
        return parse_with_docling(file_bytes, inner_filename, fmt=_DOCLING_FMT[file_type])

    if file_type == "image":
        from app.parsers.image_parser import parse
        return parse(file_bytes, inner_filename)

    if file_type == "email":
        from app.parsers.email_parser import parse
        return parse(file_bytes, inner_filename)

    if file_type == "msg":
        from app.parsers.msg_parser import parse
        return parse(file_bytes, inner_filename)

    if file_type == "csv":
        from app.parsers.csv_parser import parse
        return parse(file_bytes, inner_filename)

    if file_type == "excel":
        from app.parsers.excel_parser import parse
        return parse(file_bytes, inner_filename)

    if file_type == "xml":
        from app.parsers.xml_parser import parse
        return parse(file_bytes, inner_filename)

    if file_type == "chat":
        from app.parsers.chat_parser import parse
        return parse(file_bytes, inner_filename)

    if file_type == "zip" and depth < _MAX_DEPTH:
        # Recursive ZIP extraction (limited depth)
        return parse(file_bytes, inner_filename, _depth=depth + 1)

    if file_type in {"txt"}:
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("latin-1", errors="replace")

    return f"[Skipped: {inner_filename} (type={file_type} — not supported for text extraction)]"


def parse(file_bytes: bytes, filename: str = "archive.zip", _depth: int = 0) -> str:
    """
    Extract all text evidence from a ZIP archive.

    Args:
        file_bytes: Raw .zip bytes.
        filename:   Original filename (for context header).
        _depth:     Internal depth counter (do not set manually).

    Returns:
        Concatenated text from all parseable files inside the ZIP.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        return f"[ZIP Evidence: {filename}]\n(Invalid ZIP file: {exc})"

    output_lines: list[str] = [f"[ZIP Archive Evidence: {filename}]"]

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]

        if not members:
            return f"[ZIP Archive Evidence: {filename}]\n(Archive contains no files)"

        output_lines.append(f"Contains {len(members)} file(s):")
        for m in members:
            output_lines.append(f"  - {m.filename} ({m.file_size:,} bytes)")
        output_lines.append("")

        for member in members:
            output_lines.append(f"\n── Extracted: {member.filename} ──")
            try:
                inner_bytes = zf.read(member.filename)
                extracted_text = _parse_inner_file(inner_bytes, member.filename, depth=_depth)
                output_lines.append(extracted_text.strip())
            except Exception as exc:
                output_lines.append(f"[Error reading {member.filename}: {exc}]")

    return "\n".join(output_lines).strip()
