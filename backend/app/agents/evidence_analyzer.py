"""
agents/evidence_analyzer.py
-----------------------------
Agent 02 — Evidence Analyzer.

1. Read the indexed chunks from Qdrant Cloud and route each one:
     structured chunks (record lines, tables) → covered exactly by the Case model
     narrative chunks (letters, statements, reports) → LLM extraction of people,
       organisations, places, key quotes and plain-English findings
2. Build the shared Case model (records + identity registry, enriched with the
   people the LLM found in prose) and store it on the pipeline context.
3. Produce one entity profile per evidence item.

LLM replies are validated against a schema; quotes that do not appear verbatim in
their chunk and findings that assert guilt or intent are discarded. If an LLM
batch fails it is split in half and retried; a single chunk that still fails falls
back to rule-based extraction of its factual sentences (counted as a fallback).
Every finding and quote records the method that produced it.
"""

from __future__ import annotations

import asyncio
import json
import re

from pydantic import BaseModel

from app.agents.base import AgentResult, AgentRun, PipelineContext
from app.database import collections
from app.services.case_model import Case
from app.services.llm_service import chat_completion_json, estimate_tokens, failure_reason
from app.services.validation import ACCUSATORY
from app.services.values import find_amounts, find_dates, find_ids, format_amount, split_sentences
from app.services.vector_store import get_investigation_chunks

_SYSTEM_PROMPT = """You are a forensic evidence analyst. For each evidence chunk, extract only what the text explicitly states.

Return JSON: {"chunks": [{"ci": "<chunk id>", "p": [], "o": [], "l": [], "s": [], "f": []}]}
  p = people's names exactly as written
  o = organisations
  l = places / sites / addresses
  s = up to 3 short verbatim quotes that matter to an investigation
  f = up to 3 plain-English findings, one sentence each, directly supported by the chunk

Rules: never guess or add outside knowledge; never state guilt or intent; use [] when nothing applies."""

class _ChunkExtraction(BaseModel):
    ci: str
    p: list[str] = []
    o: list[str] = []
    l: list[str] = []
    s: list[str] = []
    f: list[str] = []


class _Reply(BaseModel):
    chunks: list[_ChunkExtraction] = []


_BATCH_INPUT_TOKENS = 3500
_OUTPUT_TOKENS_PER_CHUNK = 260
_STRUCTURED_LINE = re.compile(r"^(?:\[(?:Row|Record|Message|Event|Sheet)[^\]]*\]|\||Columns:|Total rows:|Total records:)")


async def run(ctx: PipelineContext, agent: AgentRun) -> AgentResult:
    await agent.log("scan", "Loading indexed evidence chunks from Qdrant Cloud...")
    chunks = await asyncio.to_thread(get_investigation_chunks, investigation_id=ctx.investigation_id)
    evidence_docs = await collections.evidence().find(
        {"investigation_id": ctx.investigation_id, "content": {"$nin": ["", None]}}
    ).to_list(length=None)
    if not chunks or not evidence_docs:
        return AgentResult({}, ["No indexed evidence"], "blocked", "No indexed evidence was found to analyse.")

    narrative = [c for c in chunks if not _is_structured(c["text"])]
    await agent.log("info", f"{len(chunks)} chunk(s): {len(chunks) - len(narrative)} structured, {len(narrative)} narrative.")

    extracted, fallback = await _analyse_narrative(narrative, agent)
    if not narrative:
        await agent.log("info", "No narrative text — structured records are analysed by rules only.")

    case = Case.build(evidence_docs, prose_people={eid: data["p"] for eid, data in extracted.items()})
    ctx.case = case
    await agent.log("ok", f"Read {len(case.records)} structured record(s) and identified "
                          f"{len(case.registry.entities)} people/organisations.")

    profiles = _profiles(case, extracted)
    findings = _dedupe([{"text": f, "evidence_id": eid, "method": data["method"]}
                        for eid, data in extracted.items() for f in data["f"]])
    statements = _dedupe([{"text": s, "evidence_id": eid, "method": data["method"]}
                          for eid, data in extracted.items() for s in data["s"]])
    people = {name for p in profiles.values() for name in p["people"]}
    orgs = {name for p in profiles.values() for name in p["organisations"]}

    await agent.log("ok", f"Complete: {len(people)} people, {len(orgs)} organisations, "
                          f"{len(findings)} notes from narrative text (checked later by the validator).")
    output = {
        "evidence": profiles,
        "findings": findings,
        "statements": statements,
        "people": sorted(people),
        "organisations": sorted(orgs),
        "chunks": {"total": len(chunks), "narrative": len(narrative), "fallback": fallback},
    }
    summary = [f"{len(people)} people", f"{len(orgs)} organisations", f"{len(case.records)} records", f"{len(findings)} notes"]
    counts = {"chunks": len(chunks), "narrative_chunks": len(narrative), "fallback_chunks": fallback,
              "records": len(case.records), "entities": len(case.registry.entities)}
    return AgentResult(output, summary, agent.status, counts=counts)


def _is_structured(text: str) -> bool:
    lines = [line for line in text.split("\n") if line.strip()]
    structured = sum(bool(_STRUCTURED_LINE.match(line.strip())) for line in lines)
    return bool(lines) and structured / len(lines) >= 0.6


# ── LLM extraction ────────────────────────────────────────────────────────────

async def _analyse_narrative(chunks: list[dict], agent: AgentRun) -> tuple[dict[str, dict], int]:
    """Return ({evidence_id: {p,o,l,s,f,method}}, fallback_chunk_count)."""
    extracted: dict[str, dict] = {}
    fallback = 0
    batches = _batches(chunks)
    for number, batch in enumerate(batches, start=1):
        await agent.log("scan", f"Analysing narrative batch {number}/{len(batches)} ({_label(batch)})...")
        results, failed, reason = await _extract(batch)
        for chunk in failed:
            results[_chunk_id(chunk)] = {"s": _factual_sentences(chunk["text"])}
        if failed:
            fallback += len(failed)
            await agent.fallback(f"{_label(failed)}: AI extraction unavailable ({reason}); used rule-based extraction.",
                                 units=len(failed))
        failed_ids = {_chunk_id(c) for c in failed}
        for chunk in batch:
            data = results.get(_chunk_id(chunk), {})
            bucket = extracted.setdefault(chunk["evidence_id"], {**{key: [] for key in "polsf"}, "methods": set()})
            bucket["methods"].add("rules" if _chunk_id(chunk) in failed_ids else "ai")
            for key in "polsf":
                values = [str(v).strip() for v in data.get(key, []) if str(v).strip()]
                if key == "s":
                    values = [v for v in values if _squash(v) in _squash(chunk["text"])]   # verbatim quotes only
                if key in "sf":
                    values = [v for v in values if not ACCUSATORY.search(v)]
                bucket[key].extend(values)
    for bucket in extracted.values():
        bucket["method"] = "+".join(sorted(bucket.pop("methods")))       # ai | rules | ai+rules
    return extracted, fallback


async def _extract(batch: list[dict]) -> tuple[dict[str, dict], list[dict], str]:
    """LLM-extract one batch; split in half on failure. Returns (results, failed chunks, failure reason)."""
    payload = [{"ci": _chunk_id(c), "text": c["text"]} for c in batch]
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "Evidence chunks:\n" + json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        reply = await chat_completion_json(messages, max_tokens=_OUTPUT_TOKENS_PER_CHUNK * len(batch) + 200,
                                           purpose="analyzer.extract", schema=_Reply)
        return {item["ci"]: item for item in reply["chunks"]}, [], ""
    except Exception as exc:  # empty/invalid reply, oversize request or provider error
        if len(batch) == 1:
            return {}, batch, failure_reason(exc)
        middle = len(batch) // 2
        first, first_failed, first_reason = await _extract(batch[:middle])
        second, second_failed, second_reason = await _extract(batch[middle:])
        return {**first, **second}, first_failed + second_failed, first_reason or second_reason


def _batches(chunks: list[dict]) -> list[list[dict]]:
    batches: list[list[dict]] = []
    tokens = 0
    for chunk in chunks:
        size = estimate_tokens(chunk["text"]) + 30
        if batches and tokens + size <= _BATCH_INPUT_TOKENS and batches[-1][-1]["evidence_id"] == chunk["evidence_id"]:
            batches[-1].append(chunk)
            tokens += size
        else:
            batches.append([chunk])
            tokens = size
    return batches


def _factual_sentences(text: str) -> list[str]:
    """Rule-based fallback: sentences that cite an identifier, amount or date."""
    return [s for s in split_sentences(text) if 30 <= len(s) <= 300 and (find_ids(s) or find_amounts(s) or find_dates(s))][:3]


# ── Profiles ──────────────────────────────────────────────────────────────────

def _profiles(case: Case, extracted: dict[str, dict]) -> dict[str, dict]:
    profiles = {}
    for item in case.evidence:
        data = extracted.get(item.evidence_id, {})
        mentioned = [case.registry.get(key) for key in case.registry.mentions(item.text)]
        records = [r for r in case.records if r.evidence_id == item.evidence_id]
        locations = data.get("l", []) + [
            value for r in records for key, value in r.fields.items()
            if re.search(r"location|address|site", key, re.IGNORECASE)
        ]
        profiles[item.evidence_id] = {
            "filename": item.filename,
            "people": sorted({e.name for e in mentioned if e.kind == "person"}),
            "organisations": sorted({e.name for e in mentioned if e.kind == "organisation"} | set(data.get("o", []))),
            "locations": _unique(locations)[:15],
            "identifiers": find_ids(item.text)[:80],
            "amounts": _unique([format_amount(a, case.currency) for a in find_amounts(item.text)])[:30],
            "records": len(records),
            "statements": _unique(data.get("s", []))[:6],
            "findings": _unique(data.get("f", []))[:6],
        }
    return profiles


# ── Small helpers ─────────────────────────────────────────────────────────────

def _chunk_id(chunk: dict) -> str:
    return f"{chunk['evidence_id']}#{chunk.get('chunk_index', 0)}"


def _label(chunks: list[dict]) -> str:
    return ", ".join(sorted({c["evidence_id"] for c in chunks}))


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for item in items:
        key = re.sub(r"\W+", " ", item["text"].casefold()).strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique
