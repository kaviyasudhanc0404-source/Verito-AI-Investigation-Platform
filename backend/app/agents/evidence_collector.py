"""
agents/evidence_collector.py
------------------------------
Agent 01 — Evidence Collector.

For every uploaded file:  parse → clean → chunk → embed → store in Qdrant Cloud,
then save the cleaned text and an ingestion report on the evidence document.
Parsing is dispatched by app.parsers.parse_evidence (one table for all types).

Nothing is dropped silently. Every file gets an ingestion report:
  status      indexed | indexed_with_warnings | empty | failed
  filename, format, characters, pages, records, chunks, ocr (bool), warnings[], error
and the report's evidence inventory shows it.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

from app.agents.base import AgentResult, AgentRun, PipelineContext
from app.database import collections
from app.parsers import PARSE_WARNING, parse_evidence
from app.services.chunking_service import chunk_text
from app.services.records import extract_records
from app.services.text_cleaner import clean
from app.services.vector_store import store_evidence_chunks
from app.utils.datetime_utils import utcnow
from app.utils.file_detector import detect_file_type

_STORE_ATTEMPTS = 4
_RETRYABLE = ("name resolution", "getaddrinfo", "connection", "timed out", "timeout", "429", "502", "503", "504")
_STRUCTURED_TYPES = {"csv", "excel", "json", "xml"}


async def run(ctx: PipelineContext, agent: AgentRun) -> AgentResult:
    evidence_docs = await collections.evidence().find(
        {"investigation_id": ctx.investigation_id}, sort=[("evidence_id", 1)]
    ).to_list(length=None)
    await agent.log("info", f"Found {len(evidence_docs)} evidence file(s) to process.")
    if not evidence_docs:
        return AgentResult({"evidence_ids": [], "failed_ids": [], "files": {}}, ["No evidence uploaded"], "blocked",
                           "No evidence files were uploaded for this investigation.")

    files: dict[str, dict] = {}
    for ev in evidence_docs:
        ev_id, filename = ev["evidence_id"], ev["filename"]
        await agent.log("scan", f"{ev_id}: processing {filename}")
        report = await _ingest(ctx, ev)
        files[ev_id] = report
        await collections.evidence().update_one({"_id": ev["_id"]}, {"$set": {"ingestion": report, "updated_at": utcnow()}})
        if report["status"] == "failed":
            await agent.log("error", f"{ev_id}: failed — {report['error']}")
        elif report["status"] == "empty":
            await agent.log("warn", f"{ev_id}: no readable text found in {filename}")
        else:
            detail = f"{report['chunks']} chunk(s), {report['records']} record(s)"
            await agent.log("ok", f"{ev_id}: {detail} stored")
            for warning in report["warnings"]:
                await agent.log("warn", f"{ev_id}: {warning}")

    indexed = [i for i, r in files.items() if r["status"].startswith("indexed")]
    failed = [i for i, r in files.items() if i not in indexed]
    await agent.log("ok", f"Complete: {len(indexed)} indexed, {len(failed)} not analysed.")
    summary = [f"{len(indexed)} items indexed"] + ([f"{len(failed)} failed"] if failed else [])
    counts = {"files": len(files), "indexed": len(indexed), "failed": len(failed),
              "records": sum(r["records"] for r in files.values()), "chunks": sum(r["chunks"] for r in files.values())}
    status = "failed" if not indexed else agent.status
    return AgentResult({"evidence_ids": indexed, "failed_ids": failed, "files": files}, summary, status,
                       "None of the evidence files could be read.", counts)


async def _ingest(ctx: PipelineContext, ev: dict) -> dict:
    """Parse, clean, chunk and store one evidence file. Returns its ingestion report (never raises)."""
    filename = ev["filename"]
    report = {"status": "failed", "filename": filename, "format": Path(filename).suffix.lstrip(".").lower(),
              "characters": 0, "pages": 0, "records": 0, "chunks": 0, "ocr": False, "warnings": [], "error": ""}
    file_path = ev.get("file_path", "")
    if not file_path or not Path(file_path).exists():
        report["error"] = "the file is missing on disk"
        return report
    try:
        text = clean(await parse_evidence(file_path, filename))
    except Exception as exc:
        report["error"] = _short(exc)
        return report

    report.update(_profile(filename, text))
    chunks = await asyncio.to_thread(chunk_text, text)
    if not chunks:
        report["status"] = "empty"
        return report
    try:
        report["chunks"] = await _store(ctx, ev, text, chunks)
    except Exception as exc:
        report["error"] = f"could not be indexed: {_short(exc)}"
        return report
    await collections.evidence().update_one({"_id": ev["_id"]}, {"$set": {"content": text, "updated_at": utcnow()}})
    report["status"] = "indexed_with_warnings" if report["warnings"] else "indexed"
    return report


def _profile(filename: str, text: str) -> dict:
    """What was extracted, and anything a reader should know about its reliability."""
    file_type = detect_file_type(filename)
    records = len(extract_records("", filename, text))
    ocr = file_type == "image" or "[Image OCR Text]" in text
    warnings = [f"{m['text'][:1].upper()}{m['text'][1:]}." for m in PARSE_WARNING.finditer(text)]
    if "�" in text:
        warnings.append("Some characters could not be decoded, so parts of the text may be missing.")
    if file_type in _STRUCTURED_TYPES and text.strip() and not records:
        warnings.append("No structured records were recognised; the file was analysed as plain text.")
    if ocr:
        warnings.append("Text was read with OCR and may contain recognition errors.")
    return {"characters": len(text), "pages": text.count("\n[Page ") + text.startswith("[Page "),
            "records": records, "ocr": ocr, "warnings": warnings}


async def _store(ctx: PipelineContext, ev: dict, text: str, chunks: list[str]) -> int:
    for attempt in range(1, _STORE_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(
                store_evidence_chunks,
                text=text, chunks=chunks, user_id=ev.get("user_id", ""),
                investigation_id=ctx.investigation_id, evidence_id=ev["evidence_id"], filename=ev["filename"],
            )
        except Exception as exc:
            retryable = isinstance(exc, (TimeoutError, ConnectionError, socket.gaierror)) or \
                any(marker in str(exc).lower() for marker in _RETRYABLE)
            if attempt == _STORE_ATTEMPTS or not retryable:
                raise
            await asyncio.sleep(min(2 ** attempt, 8))
    return 0


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:160] or exc.__class__.__name__
