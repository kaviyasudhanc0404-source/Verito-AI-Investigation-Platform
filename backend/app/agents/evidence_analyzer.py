"""
agents/evidence_analyzer.py
----------------------------
Agent 02 - Evidence Analyzer

Reads indexed evidence chunks from Qdrant Cloud, analyzes each chunk with the
configured Groq model, and stores structured forensic entities/findings in
MongoDB for downstream agents.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app.database import collections
from app.services.llm_service import chat_completion_json
from app.services.vector_store import get_investigation_chunks
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

_SYSTEM_PROMPT = """You are a forensic evidence analyst.
Analyze the provided evidence chunk and extract structured entities.

Return ONLY valid JSON in exactly this format:
{
  "people": ["name1", "name2"],
  "organisations": ["org1"],
  "locations": ["location1"],
  "dates": ["date1"],
  "times": ["time1"],
  "contact_info": ["phone/email/url/account"],
  "key_statements": ["important quote or fact"],
  "key_findings": ["finding description"]
}

Rules:
- Extract only what is explicitly present in the chunk.
- Do not invent names, places, dates, relationships, or conclusions.
- Keep statements and findings short, factual, and evidence-grounded.
- Put phone numbers, emails, URLs, handles, and account identifiers in contact_info.
- If a category is absent, return an empty list for that category."""

_ENTITY_KEYS = (
    "people",
    "organisations",
    "locations",
    "dates",
    "times",
    "contact_info",
    "key_statements",
    "key_findings",
)

_MAX_CHUNK_CHARS = 7000
_MAX_RETRIES = 3


async def run(
    investigation_id: str,
    emit: Emitter,
) -> dict:
    """Run the Evidence Analyzer agent."""
    agent_id = "analyzer"
    await _set_status(investigation_id, agent_id, "running")
    lines: list[dict] = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    await add_line("scan", "Fetching indexed evidence chunks from Qdrant Cloud...")

    try:
        chunks = await asyncio.to_thread(
            get_investigation_chunks,
            investigation_id=investigation_id,
        )
    except Exception:
        await add_line("error", "Could not fetch indexed chunks from Qdrant Cloud.")
        output = _empty_output()
        await _complete(investigation_id, agent_id, lines, ["Qdrant chunk fetch failed"], output)
        await emit("agent_completed", {"agent_id": agent_id})
        return output

    await add_line("info", f"Loaded {len(chunks)} chunk(s) for analysis.")

    if not chunks:
        await add_line("warn", "No indexed chunks found. Run the collector before analyzer.")
        output = _empty_output()
        await _complete(investigation_id, agent_id, lines, ["No chunks analyzed"], output)
        await emit("agent_completed", {"agent_id": agent_id})
        return output

    output = _empty_output()

    for index, chunk in enumerate(chunks, start=1):
        evidence_id = str(chunk.get("evidence_id") or "unknown")
        filename = str(chunk.get("filename") or "unknown")
        chunk_index = int(chunk.get("chunk_index") or 0)
        text = _normalise_text(chunk.get("text", ""))

        if len(text) < 20:
            await add_line("warn", f"{evidence_id} chunk {chunk_index}: insufficient text")
            _add_failed_chunk(output, evidence_id, filename, chunk_index, "insufficient_text")
            continue

        await add_line(
            "scan",
            f"Analysing {evidence_id} chunk {index}/{len(chunks)}...",
        )

        try:
            entities = await _analyse_chunk_with_retries(
                evidence_id=evidence_id,
                filename=filename,
                chunk_index=chunk_index,
                text=text,
            )
        except Exception as exc:
            reason = "rate_limited" if _is_rate_limit_error(exc) else "analysis_failed"
            _add_failed_chunk(output, evidence_id, filename, chunk_index, reason)
            await add_line(
                "warn",
                f"{evidence_id} chunk {index}/{len(chunks)} could not be analysed.",
            )
            continue

        output["chunks_analyzed"] += 1
        bucket = _ensure_evidence_bucket(output, evidence_id, filename)

        for key in _ENTITY_KEYS:
            values = entities.get(key, [])
            if not isinstance(values, list):
                continue

            for value in values:
                cleaned = _normalise_text(value)
                if not cleaned:
                    continue

                _add_unique(output[key], cleaned)
                _add_unique(bucket[key], cleaned)
                _add_reference(output, key, cleaned, evidence_id, filename, chunk_index)

        await asyncio.sleep(0.05)

    total_entities = sum(len(output[key]) for key in _ENTITY_KEYS)
    await add_line(
        "ok",
        (
            f"Complete: {output['chunks_analyzed']} chunk(s) analyzed, "
            f"{output['chunks_failed']} failed, {total_entities} total items found."
        ),
    )

    summary = [
        f"{len(output['people'])} people",
        f"{len(output['locations'])} locations",
        f"{len(output['dates'])} dates",
        f"{len(output['key_findings'])} findings",
    ]

    await _complete(investigation_id, agent_id, lines, summary, output)
    await emit("agent_completed", {"agent_id": agent_id})

    return output


def _empty_output() -> dict:
    return {
        "people": [],
        "organisations": [],
        "locations": [],
        "dates": [],
        "times": [],
        "contact_info": [],
        "key_statements": [],
        "key_findings": [],
        "entities_by_evidence": {},
        "referenced_key_statements": [],
        "referenced_key_findings": [],
        "failed_chunks": [],
        "chunks_analyzed": 0,
        "chunks_failed": 0,
    }


def _normalise_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _add_unique(target: list[str], value: object) -> bool:
    cleaned = _normalise_text(value)
    if not cleaned:
        return False

    existing = {item.casefold() for item in target}
    if cleaned.casefold() in existing:
        return False

    target.append(cleaned)
    return True


def _ensure_evidence_bucket(output: dict, evidence_id: str, filename: str) -> dict:
    buckets = output["entities_by_evidence"]
    if evidence_id not in buckets:
        buckets[evidence_id] = {
            "filename": filename,
            "people": [],
            "organisations": [],
            "locations": [],
            "dates": [],
            "times": [],
            "contact_info": [],
            "key_statements": [],
            "key_findings": [],
        }
    return buckets[evidence_id]


def _add_reference(
    output: dict,
    key: str,
    text: str,
    evidence_id: str,
    filename: str,
    chunk_index: int,
) -> None:
    if key not in {"key_statements", "key_findings"}:
        return

    target_key = (
        "referenced_key_statements"
        if key == "key_statements"
        else "referenced_key_findings"
    )
    candidate = {
        "text": text,
        "evidence_id": evidence_id,
        "filename": filename,
        "chunk_index": chunk_index,
    }
    existing = {
        (
            item.get("text", "").casefold(),
            item.get("evidence_id", ""),
            item.get("chunk_index", 0),
        )
        for item in output[target_key]
    }
    identity = (text.casefold(), evidence_id, chunk_index)
    if identity not in existing:
        output[target_key].append(candidate)


def _add_failed_chunk(
    output: dict,
    evidence_id: str,
    filename: str,
    chunk_index: int,
    reason: str,
) -> None:
    output["chunks_failed"] += 1
    output["failed_chunks"].append(
        {
            "evidence_id": evidence_id,
            "filename": filename,
            "chunk_index": chunk_index,
            "reason": reason,
        }
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


async def _analyse_chunk_with_retries(
    *,
    evidence_id: str,
    filename: str,
    chunk_index: int,
    text: str,
) -> dict:
    user_prompt = (
        f"Evidence ID: {evidence_id}\n"
        f"Filename: {filename}\n"
        f"Chunk index: {chunk_index}\n\n"
        f"Evidence chunk:\n{text[:_MAX_CHUNK_CHARS]}"
    )

    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result = await chat_completion_json(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=1800,
            )
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            last_error = exc
            if not _is_rate_limit_error(exc) or attempt == _MAX_RETRIES:
                break
            await asyncio.sleep(5 * attempt)

    raise last_error or RuntimeError("Unknown analysis error")


async def _set_status(investigation_id: str, agent_id: str, status: str) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {"$set": {"status": status, "updated_at": utcnow()}},
        upsert=True,
    )


async def _append_line(investigation_id: str, agent_id: str, line: dict) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {"$push": {"lines": line}, "$set": {"updated_at": utcnow()}},
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
