"""
agents/evidence_collector.py
------------------------------
Agent 01 — Evidence Collector (Full Pipeline)

Orchestrates the complete evidence ingestion pipeline for an investigation:
  1. Fetch all uploaded evidence documents from MongoDB
  2. Read file bytes from disk
  3. Dispatch to the correct parser based on detected file type
  4. Clean extracted text (two-phase evidence-preserving cleaner)
  5. Chunk into semantically coherent segments (structure-aware chunker)
  6. Generate embeddings (BAAI/bge-small-en-v1.5)
  7. Store vectors in Qdrant Cloud
  8. Update evidence content in MongoDB
  9. Emit SSE activity lines throughout

File Type → Parser Mapping:
  .pdf .docx .pptx .html .md     → parsers/docling_parser.py   (Docling unified)
  .jpg .jpeg .png .bmp .tiff .webp → parsers/image_parser.py  (EasyOCR + Gemini)
  .eml                           → parsers/email_parser.py     (Python stdlib email)
  .msg                           → parsers/msg_parser.py       (extract-msg)
  .csv                           → parsers/csv_parser.py       (Python stdlib csv)
  .xlsx / .xls                   → parsers/excel_parser.py     (pandas + openpyxl)
  .xml                           → parsers/xml_parser.py       (lxml / ElementTree)
  .json / .txt (chat logs)       → parsers/chat_parser.py      (custom)
  .zip                           → parsers/zip_extractor.py    (zipfile + recursive)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Awaitable

from app.database import collections
from app.utils.datetime_utils import utcnow
from app.utils.file_detector import detect_file_type

# Type for the SSE emit callback
Emitter = Callable[[str, dict], Awaitable[None]]

# All Docling-supported formats mapped to their InputFormat fmt string.
# One function handles all of them — no separate wrapper files needed.
_DOCLING_FMT: dict[str, str] = {
    "pdf":      "pdf",
    "docx":     "docx",
    "doc":      "docx",
    "pptx":     "pptx",
    "html":     "html",
    "markdown": "md",
}


# ── Parser dispatch ───────────────────────────────────────────────────────────

def _parse_evidence(file_bytes: bytes, filename: str, file_type: str) -> str:
    """
    Dispatch to the correct parser based on detected file type.

    Args:
        file_bytes: Raw file bytes.
        filename:   Original filename.
        file_type:  Detected type string from file_detector.

    Returns:
        Extracted plain text (may be empty if unsupported).
    """
    # ── Docling: one function handles PDF / DOCX / PPTX / HTML / Markdown ────
    if file_type in _DOCLING_FMT:
        from app.parsers.docling_parser import parse_with_docling
        return parse_with_docling(file_bytes, filename, fmt=_DOCLING_FMT[file_type])

    # ── Image: EasyOCR + Gemini ──────────────────────────────────────────────
    if file_type == "image":
        from app.parsers.image_parser import parse
        return parse(file_bytes, filename)

    # ── Email parsers ─────────────────────────────────────────────────────────
    if file_type == "email":
        from app.parsers.email_parser import parse
        return parse(file_bytes, filename)

    if file_type == "msg":
        from app.parsers.msg_parser import parse
        return parse(file_bytes, filename)

    # ── Tabular data ──────────────────────────────────────────────────────────
    if file_type == "csv":
        from app.parsers.csv_parser import parse
        return parse(file_bytes, filename)

    if file_type == "excel":
        from app.parsers.excel_parser import parse
        return parse(file_bytes, filename)

    # ── Structured data ───────────────────────────────────────────────────────
    if file_type == "xml":
        from app.parsers.xml_parser import parse
        return parse(file_bytes, filename)

    # ── Chat logs (JSON / TXT) ────────────────────────────────────────────────
    if file_type == "chat":
        from app.parsers.chat_parser import parse
        return parse(file_bytes, filename)

    # ── Archive ───────────────────────────────────────────────────────────────
    if file_type == "zip":
        from app.parsers.zip_extractor import parse
        return parse(file_bytes, filename)

    # ── Plain text fallback ───────────────────────────────────────────────────
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return file_bytes.decode("latin-1", errors="replace")


# ── Agent entry point ─────────────────────────────────────────────────────────

async def run(
    investigation_id: str,
    emit: Emitter,
) -> dict:
    """
    Run the Evidence Collector agent for an investigation.

    Processes all uploaded evidence files:
      parse → clean → chunk → embed → store in Qdrant Cloud

    Args:
        investigation_id: The investigation to process.
        emit: Async SSE callback — emit(event_name, payload).

    Returns:
        Structured output dict with processing summary.
    """
    agent_id = "collector"

    await _set_status(investigation_id, agent_id, "running")

    lines: list[dict] = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    # ── 1. Fetch evidence documents from MongoDB ──────────────────────────────
    evidence_docs = await collections.evidence().find(
        {"investigation_id": investigation_id}
    ).to_list(length=None)

    await add_line("info", f"Found {len(evidence_docs)} evidence item(s) to process.")

    if not evidence_docs:
        await add_line("warn", "No evidence found for this investigation.")
        output = {"items_indexed": 0, "items_failed": 0, "evidence_ids": []}
        await _complete(investigation_id, agent_id, lines, ["0 items indexed"], output)
        await emit("agent_completed", {"agent_id": agent_id})
        return output

    # ── 2. Process each evidence item ─────────────────────────────────────────
    evidence_ids: list[str] = []
    failed_ids: list[str] = []

    for ev in evidence_docs:
        ev_id: str = ev["evidence_id"]
        filename: str = ev["filename"]
        file_path: str = ev.get("file_path", "")

        await add_line("scan", f"Processing {filename} [{ev_id}]...")

        try:
            # ── 2a. Read file bytes from disk ──────────────────────────────
            if not file_path or not Path(file_path).exists():
                await add_line("warn", f"{ev_id}: File not found on disk — {file_path}")
                failed_ids.append(ev_id)
                continue

            file_bytes = Path(file_path).read_bytes()

            # ── 2b. Detect file type ───────────────────────────────────────
            file_type = detect_file_type(filename)
            await add_line("info", f"{ev_id}: Detected type → {file_type}")

            # ── 2c. Parse (in thread to avoid blocking event loop) ─────────
            raw_text: str = await asyncio.to_thread(
                _parse_evidence, file_bytes, filename, file_type
            )

            if not raw_text.strip():
                await add_line("warn", f"{ev_id}: No text extracted from {filename}")
                failed_ids.append(ev_id)
                continue

            await add_line("ok", f"{ev_id}: Extracted {len(raw_text):,} characters")

            # ── 2d. Clean text (two-phase evidence-preserving cleaner) ──────
            from app.services.text_cleaner import clean
            cleaned_text = await asyncio.to_thread(clean, raw_text)

            # ── 2e. Chunk (structure-aware semantic chunker) ────────────────
            from app.services.chunking_service import chunk_text
            chunks = await asyncio.to_thread(chunk_text, cleaned_text)
            await add_line("info", f"{ev_id}: Split into {len(chunks)} chunk(s)")

            if not chunks:
                await add_line("warn", f"{ev_id}: No valid chunks produced from {filename}")
                failed_ids.append(ev_id)
                continue

            # ── 2f. Embed + Store in Qdrant Cloud ──────────────────────────
            # Pass pre-computed chunks — avoids double chunking inside store_evidence_chunks
            from app.services.vector_store import store_evidence_chunks
            stored_count: int = await asyncio.to_thread(
                store_evidence_chunks,
                text=cleaned_text,
                chunks=chunks,
                user_id=ev.get("user_id", ""),
                investigation_id=investigation_id,
                evidence_id=ev_id,
                filename=filename,
            )
            await add_line("ok", f"{ev_id}: Stored {stored_count} vector(s) in Qdrant Cloud")

            # ── 2g. Update content in MongoDB ──────────────────────────────
            await collections.evidence().update_one(
                {"evidence_id": ev_id, "investigation_id": investigation_id},
                {"$set": {"content": cleaned_text, "updated_at": utcnow()}},
            )

            evidence_ids.append(ev_id)
            await asyncio.sleep(0.1)  # Yield to event loop for SSE streaming

        except Exception as exc:
            await add_line("error", f"{ev_id}: Failed — {exc}")
            failed_ids.append(ev_id)

    # ── 3. Summary ────────────────────────────────────────────────────────────
    await add_line(
        "ok",
        f"Complete: {len(evidence_ids)} indexed, {len(failed_ids)} failed.",
    )

    summary_lines = [f"{len(evidence_ids)} items indexed"]
    if failed_ids:
        summary_lines.append(f"{len(failed_ids)} failed: {', '.join(failed_ids)}")
    if evidence_ids:
        if len(evidence_ids) > 1:
            summary_lines.append(f"IDs: {evidence_ids[0]} – {evidence_ids[-1]}")
        else:
            summary_lines.append(f"ID: {evidence_ids[0]}")

    output = {
        "items_indexed": len(evidence_ids),
        "items_failed": len(failed_ids),
        "evidence_ids": evidence_ids,
        "failed_ids": failed_ids,
    }

    await _complete(investigation_id, agent_id, lines, summary_lines, output)
    await emit("agent_completed", {"agent_id": agent_id})

    return output


# ── MongoDB helpers ───────────────────────────────────────────────────────────

async def _set_status(investigation_id: str, agent_id: str, status: str) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {
            "$set": {
                "status": status,
                "updated_at": utcnow(),
            }
        },
        upsert=True,
    )


async def _append_line(investigation_id: str, agent_id: str, line: dict) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {
            "$push": {"lines": line},
            "$set": {"updated_at": utcnow()},
        },
    )


async def _complete(
    investigation_id: str,
    agent_id: str,
    lines: list[dict],
    summary: list[str],
    output: dict,
) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {
            "$set": {
                "status": "done",
                "summary": summary,
                "output": output,
                "updated_at": utcnow(),
            }
        },
    )
