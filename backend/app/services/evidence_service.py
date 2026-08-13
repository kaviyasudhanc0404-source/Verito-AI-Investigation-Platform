"""
services/evidence_service.py
------------------------------
Upload and store evidence file metadata.

Upload phase per file:
  1. Detect kind and save to disk
  2. Detect granular file type
  3. Store metadata in MongoDB

Parsing, cleaning, chunking, embedding, and Qdrant storage are owned by the
Evidence Collector agent so progress can be streamed to the frontend.
"""

import asyncio
from pathlib import Path

from app.database import collections
from app.utils.datetime_utils import utcnow
from app.utils.file_utils import save_upload, detect_kind
from app.utils.file_detector import detect_file_type
from app.utils.id_generator import new_evidence_db_id, format_evidence_id


async def _get_next_evidence_index(investigation_id: str) -> int:
    """Return the next available evidence sequence number (1-based)."""
    count = await collections.evidence().count_documents(
        {"investigation_id": investigation_id}
    )
    return count + 1


async def _extract_content(
    file_bytes: bytes,
    filename: str,
    file_type: str,
    file_path: str,
) -> str:
    """
    Dispatch to the correct parser based on file type.

    Args:
        file_bytes: Raw file bytes.
        filename: Original filename.
        file_type: Granular type from detect_file_type().
        file_path: Saved disk path (used for audio transcription).

    Returns:
        Extracted plain text.
    """
    # ── Docling: one call handles PDF / DOCX / PPTX / HTML / Markdown ────────
    _DOCLING_FMT = {
        "pdf":      "pdf",
        "docx":     "docx",
        "doc":      "docx",
        "pptx":     "pptx",
        "html":     "html",
        "markdown": "md",
    }
    if file_type in _DOCLING_FMT:
        from app.parsers.docling_parser import parse_with_docling
        return await asyncio.to_thread(
            parse_with_docling, file_bytes, filename, _DOCLING_FMT[file_type]
        )

    if file_type == "image":
        from app.parsers.image_parser import parse
        return await asyncio.to_thread(parse, file_bytes, filename)

    if file_type == "email":
        from app.parsers.email_parser import parse
        return await asyncio.to_thread(parse, file_bytes, filename)

    if file_type == "msg":
        from app.parsers.msg_parser import parse
        return await asyncio.to_thread(parse, file_bytes, filename)

    if file_type == "csv":
        from app.parsers.csv_parser import parse
        return await asyncio.to_thread(parse, file_bytes, filename)

    if file_type == "excel":
        from app.parsers.excel_parser import parse
        return await asyncio.to_thread(parse, file_bytes, filename)

    if file_type == "xml":
        from app.parsers.xml_parser import parse
        return await asyncio.to_thread(parse, file_bytes, filename)

    if file_type == "chat":
        from app.parsers.chat_parser import parse
        return await asyncio.to_thread(parse, file_bytes, filename)

    if file_type == "zip":
        from app.parsers.zip_extractor import parse
        return await asyncio.to_thread(parse, file_bytes, filename)

    if file_type == "txt":
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("latin-1", errors="replace")

    if file_type == "audio":
        from app.services.audio_service import transcribe as audio_transcribe
        return await audio_transcribe(file_path, filename)

    if file_type == "video":
        return _video_metadata(file_bytes, filename, file_path)

    return ""



def _video_metadata(file_bytes: bytes, filename: str, file_path: str) -> str:
    """Return searchable metadata string for video evidence."""
    ext = Path(filename).suffix.lower() or "unknown"
    size_mb = len(file_bytes) / (1024 * 1024)
    return "\n".join([
        f"[Video Evidence: {filename}]",
        f"File type: {ext}",
        f"Stored path: {file_path}",
        f"Size: {size_mb:.2f} MB",
        "Video content preserved for review.",
    ])


async def process_and_store_evidence(
    *,
    file_bytes: bytes,
    filename: str,
    user_id: str,
    investigation_id: str,
) -> dict:
    """
    Store a single uploaded evidence file for later collector processing:
      1. Save to disk
      2. Detect file type
      3. Store metadata in MongoDB

    The expensive extraction/chunking/embedding work happens in the Evidence
    Collector stream. Keeping upload lightweight avoids doing the same work
    twice and lets the UI show live agent progress.

    Returns:
        The evidence document dict (as stored in MongoDB).
    """
    # 1. Detect kind (broad) and save to disk
    kind = detect_kind(filename)
    file_path = save_upload(file_bytes, filename, user_id, investigation_id)

    # 2. Granular file type for parser dispatch
    file_type = detect_file_type(filename)

    # 3. Get evidence sequence number (E-001, E-002, …)
    idx = await _get_next_evidence_index(investigation_id)
    evidence_id = format_evidence_id(idx)

    # 4. Build and insert MongoDB document.
    # Content is filled by Evidence Collector after parsing and cleaning.
    doc = {
        "_id": new_evidence_db_id(),
        "investigation_id": investigation_id,
        "user_id": user_id,
        "evidence_id": evidence_id,
        "filename": filename,
        "kind": kind,
        "file_type": file_type,
        "file_path": file_path,
        "size_bytes": len(file_bytes),
        "content": "",
        "created_at": utcnow(),
    }
    await collections.evidence().insert_one(doc)

    return doc



async def get_evidence_for_investigation(investigation_id: str) -> list[dict]:
    """Fetch all evidence documents for an investigation, sorted by creation time."""
    cursor = collections.evidence().find(
        {"investigation_id": investigation_id},
        sort=[("created_at", 1)],
    )
    return await cursor.to_list(length=None)
