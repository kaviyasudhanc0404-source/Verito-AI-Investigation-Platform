"""
agents/evidence_analyzer.py
----------------------------
Agent 02 - Evidence Analyzer

Reads indexed evidence chunks from Qdrant Cloud, classifies each chunk as
'llm_required' or 'deterministic_sufficient' based on actual content signals,
then either batches LLM-required chunks into token-safe requests or runs
deterministic regex extraction.

Key design invariants:
  • Routing is content-driven — no chunk is forced into deterministic mode to
    hit an API-call target. Call count is an observed outcome, not a goal.
  • Deterministic extraction NEVER produces key_findings — findings require
    LLM semantic interpretation.
  • Every LLM failure emits structured telemetry before any retry or fallback.
  • Batch sizes are determined by the shared token budget, not fixed constants.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Awaitable, Callable

from app.database import collections
from app.services.llm_service import (
    ContextTooLargeError,
    chat_completion_json,
    emit_llm_failure_telemetry,
)
from app.services.vector_store import get_investigation_chunks
from app.utils.context_budget import (
    estimate_tokens,
    fit_chunk_text,
    get_token_budget,
    validate_payload,
)
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

# ── System prompt (compact — uses short-key schema) ───────────────────────────
_SYSTEM_PROMPT = """You are a forensic evidence analyst.
Analyze each evidence chunk and extract structured entities.

Return ONLY valid JSON:
{
  "chunks": [
    {
      "ci": <chunk_index int>,
      "p": ["person names"],
      "o": ["organisation names"],
      "l": ["location names"],
      "d": ["dates"],
      "t": ["times"],
      "c": ["phone/email/url"],
      "id": ["case/account/vendor/employee/invoice/order/transaction id"],
      "a": ["currency amounts"],
      "ks": ["important quote or direct observation from text"],
      "kf": ["derived evidence-grounded finding — ONLY if clearly supported"]
    }
  ]
}

Rules:
- Extract only what is explicitly present in the chunk.
- Do not invent names, places, dates, relationships, or conclusions.
- ks = direct observations; kf = derived conclusions (evidence-grounded only).
- contact_info (c): phones, emails, URLs, handles.
- identifiers (id): account numbers, invoice IDs, PO IDs, employee IDs,
  vendor IDs, transaction IDs, case IDs.
- If a category is absent, return an empty list for it.
- kf must be [] when there is no clearly supported conclusion."""

# Short-key → full-key mapping (applied after LLM response parsing)
_SHORT_KEY_MAP = {
    "ci": "chunk_index",
    "p": "people",
    "o": "organisations",
    "l": "locations",
    "d": "dates",
    "t": "times",
    "c": "contact_info",
    "id": "identifiers",
    "a": "amounts",
    "ks": "key_statements",
    "kf": "key_findings",
}

_ENTITY_KEYS = (
    "people",
    "organisations",
    "locations",
    "dates",
    "times",
    "contact_info",
    "identifiers",
    "amounts",
    "key_statements",
    "key_findings",
)

_MAX_RETRIES = 3
_MAX_CHUNK_TOKENS = 1500          # Per-chunk text token budget before semantic fitting
_PER_CHUNK_OUTPUT_ESTIMATE = 200  # ~tokens expected in LLM output per chunk
_LLM_OUTPUT_ENVELOPE = 150        # JSON envelope overhead in output token estimate

# ── Regex patterns ────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()]+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_AMOUNT_RE = re.compile(r"(?<!\w)(?:₹|Rs\.?|INR|\$|USD)\s?\d[\d,]*(?:\.\d{1,2})?", re.IGNORECASE)
_ID_RE = re.compile(
    r"\b(?:INV|PO|TXN|TRX|EMP|VEN|VENDOR|USER|ACC|ACCOUNT|CASE|AUDIT|E)-?[A-Z0-9]{3,}\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])\b")
_MONTH_DATE_RE = re.compile(
    r"\b(?:0?[1-9]|[12]\d|3[01])\s+"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s?(?:AM|PM))?(?:\s?[+-]\d{4})?\b",
    re.IGNORECASE,
)
_ORG_SUFFIX_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&.,' -]{2,}\s(?:Ltd|Limited|LLP|Inc|Corp|Corporation|Company|"
    r"Pvt|Private|Bank|Systems|Technologies|Enterprises|Solutions)\b"
)
_NARRATIVE_RE = re.compile(
    r"\b(because|therefore|however|although|suggests?|indicates?|confirms?|"
    r"contradicts?|suspicious|unusual|pattern|relationship|according|"
    r"evidence|witness|statement|alleged|reported|observed|violation|"
    r"discrepancy|unauthorized|irregular|fraudulent|complaint)\b",
    re.IGNORECASE,
)


# ── Main entry point ──────────────────────────────────────────────────────────

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
        await _complete(investigation_id, agent_id, lines, ["Qdrant chunk fetch failed"], output, status="failed")
        await emit("agent_failed", {"agent_id": agent_id, "error": "Could not fetch indexed chunks from Qdrant Cloud."})
        return output

    await add_line("info", f"Loaded {len(chunks)} chunk(s) for analysis.")

    if not chunks:
        await add_line("warn", "No indexed chunks found. Run the collector before analyzer.")
        output = _empty_output()
        await _complete(investigation_id, agent_id, lines, ["No chunks analyzed"], output, status="blocked")
        await emit("agent_blocked", {"agent_id": agent_id, "reason": "No indexed chunks found for analysis."})
        return output

    output = _empty_output()

    # ── Validate and normalise all chunks ─────────────────────────────────────
    valid_chunks: list[dict] = []
    for chunk in chunks:
        evidence_id = str(chunk.get("evidence_id") or "unknown")
        filename = str(chunk.get("filename") or "unknown")
        chunk_index = int(chunk.get("chunk_index") or 0)
        text = _normalise_text(chunk.get("text", ""))

        if len(text) < 20:
            await add_line("warn", f"{evidence_id} chunk {chunk_index}: insufficient text")
            _add_failed_chunk(output, evidence_id, filename, chunk_index, "insufficient_text")
            continue
        valid_chunks.append({
            "evidence_id": evidence_id,
            "filename": filename,
            "chunk_index": chunk_index,
            "text": text,
        })

    # ── Classify chunks: LLM-required vs deterministic-sufficient ─────────────
    llm_chunks: list[dict] = []
    det_chunks: list[dict] = []
    for chunk in valid_chunks:
        classification, reason, density = _classify_chunk(chunk["text"], chunk["filename"])
        chunk["_classification"] = classification
        chunk["_classification_reason"] = reason
        chunk["_semantic_density"] = density
        if classification == "llm_required":
            llm_chunks.append(chunk)
        else:
            det_chunks.append(chunk)

    await add_line(
        "info",
        f"Chunk routing: {len(llm_chunks)} LLM-required, "
        f"{len(det_chunks)} deterministic-sufficient "
        f"(based on content signal, not targets).",
    )

    # ── Process deterministic chunks ──────────────────────────────────────────
    for chunk in det_chunks:
        entities = _deterministic_extract(chunk["text"])
        _apply_entities(output, chunk, entities)

    if det_chunks:
        await add_line("info", f"Deterministic extraction complete for {len(det_chunks)} chunk(s).")

    # ── Build token-safe batches for LLM chunks ───────────────────────────────
    budget = get_token_budget(output_reserve=_LLM_OUTPUT_ENVELOPE + len(llm_chunks) * _PER_CHUNK_OUTPUT_ESTIMATE)
    # Clamp output_reserve to a sane maximum
    budget = get_token_budget(output_reserve=min(2400, _LLM_OUTPUT_ENVELOPE + 4 * _PER_CHUNK_OUTPUT_ESTIMATE))
    batches = _batch_chunks_by_tokens(llm_chunks, budget=budget)

    await add_line("info", f"Prepared {len(batches)} LLM analysis batch(es) for {len(llm_chunks)} chunk(s).")

    for batch_index, batch in enumerate(batches, start=1):
        label = _batch_label(batch)
        await add_line("scan", f"Analysing batch {batch_index}/{len(batches)} ({label})...")

        try:
            result = await _analyse_batch_with_retries(batch, batch_index, len(batches))
            analysed_items = _analysis_items_from_result(result, batch)
        except Exception as exc:
            # LLM completely failed — fall back to deterministic for this batch
            analysed_items = []
            fallback_count = 0
            for item in batch:
                entities = _deterministic_extract(item["text"])
                if not _has_entities(entities):
                    reason = "rate_limited" if _is_rate_limit_error(exc) else "analysis_failed"
                    _add_failed_chunk(output, item["evidence_id"], item["filename"], item["chunk_index"], reason)
                    continue
                fallback_count += 1
                analysed_items.append((item, entities))

            _code, _category = _classify_llm_error(exc)
            emit_llm_failure_telemetry(
                agent_name="evidence_analyzer",
                error_code=_code,
                error_category=_category,
                estimated_input_tokens=_estimate_batch_input_tokens(batch),
                requested_output_tokens=_output_budget_for_batch(batch, budget),
                payload_size_chars=_batch_payload_chars(batch),
                attempt_number=_MAX_RETRIES,
                batch_number=batch_index,
                batch_total=len(batches),
                evidence_ids=list({item["evidence_id"] for item in batch}),
                chunk_indices=[f"{item['evidence_id']}:{item['chunk_index']}" for item in batch],
                was_retried=True,
                retry_action="split_batch",
                fallback_action="deterministic_extraction",
                original_message=str(exc),
            )

            if not analysed_items:
                await add_line("warn", f"{label} could not be analysed: {_safe_error(exc)}")
                continue
            output["llm_fallback_chunks"] += fallback_count
            await add_line(
                "warn",
                f"{label} used deterministic extraction for {fallback_count} chunk(s) "
                f"after LLM error: {_safe_error(exc)}",
            )

        for item, entities in analysed_items:
            _apply_entities(output, item, entities)

        await asyncio.sleep(0.05)

    total_entities = sum(len(output[key]) for key in _ENTITY_KEYS)
    await add_line(
        "ok",
        f"Complete: {output['chunks_analyzed']} chunk(s) analyzed, "
        f"{output['chunks_failed']} failed, {total_entities} total items found.",
    )

    summary = [
        f"{len(output['people'])} people",
        f"{len(output['locations'])} locations",
        f"{len(output['dates'])} dates",
        f"{len(output['key_findings'])} findings",
    ]

    status = (
        "failed"
        if output["chunks_analyzed"] == 0 and output["chunks_failed"] > 0
        else "completed_with_warnings"
        if output["chunks_failed"] or output["llm_fallback_chunks"]
        else "done"
    )
    await _complete(investigation_id, agent_id, lines, summary, output, status=status)
    if status == "failed":
        await emit("agent_failed", {"agent_id": agent_id, "error": "Evidence Analyzer produced no usable chunk analyses."})
    elif status == "completed_with_warnings":
        await emit("agent_completed_with_warnings", {"agent_id": agent_id})
    else:
        await emit("agent_completed", {"agent_id": agent_id})

    return output


# ── Chunk classification ──────────────────────────────────────────────────────

def _classify_chunk(text: str, filename: str = "") -> tuple[str, str, float]:
    """Classify whether a chunk needs LLM analysis or deterministic extraction.

    Returns: (classification, reason, semantic_density)
    classification: "llm_required" | "deterministic_sufficient"

    Bias: default is LLM. Deterministic only when chunk is clearly
    structured/tabular with obvious extractable entities and no narrative content.
    Never forces deterministic to hit an API-call count.
    """
    word_count = len(text.split())

    # Very short chunks — not enough content for semantic analysis
    if word_count < 10:
        return ("deterministic_sufficient", "too_short", 0.0)

    extractable_count = (
        len(_ID_RE.findall(text))
        + len(_EMAIL_RE.findall(text))
        + len(_PHONE_RE.findall(text))
        + len(_AMOUNT_RE.findall(text))
        + len(_ISO_DATE_RE.findall(text))
        + len(_MONTH_DATE_RE.findall(text))
    )

    narrative_count = len(_NARRATIVE_RE.findall(text))

    structural_lines = sum(
        1 for line in text.split("\n")
        if "|" in line or line.count(",") >= 3 or line.count("\t") >= 2
    )
    structural_ratio = structural_lines / max(len(text.split("\n")), 1)

    semantic_density = (narrative_count * 3 + word_count - extractable_count * 5) / max(word_count, 1)
    semantic_density = max(0.0, min(1.0, semantic_density))

    # Rule 1: ANY meaningful narrative content → always LLM
    if narrative_count >= 2:
        return ("llm_required", "narrative_content", semantic_density)

    # Rule 2: Clearly tabular/structured with extractable entities → deterministic
    if structural_ratio >= 0.6 and extractable_count >= 3:
        return ("deterministic_sufficient", "structured_tabular_data", semantic_density)

    # Rule 3: Entity-dense, low semantic content → deterministic
    if semantic_density < 0.15 and extractable_count >= 5:
        return ("deterministic_sufficient", "entity_dense_low_semantic", semantic_density)

    # Default: LLM (prefer quality; never artificially force deterministic)
    return ("llm_required", "default_llm_preferred", semantic_density)


# ── Token-based batch grouping ────────────────────────────────────────────────

def _batch_chunks_by_tokens(llm_chunks: list[dict], *, budget: object) -> list[list[dict]]:
    """Group LLM-required chunks into token-safe batches.

    Context-aware: same evidence_id stays together where possible.
    Splits within evidence when chunks exceed the per-batch token budget.
    Batch size is determined solely by available tokens — not a fixed constant.
    """
    system_prompt_tokens = estimate_tokens(_SYSTEM_PROMPT)
    envelope_tokens = 80  # JSON envelope + user prompt framing
    available = budget.effective_input - system_prompt_tokens - envelope_tokens  # type: ignore[attr-defined]
    available = max(available, 200)  # Safety floor

    batches: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    current_eid: str | None = None

    for chunk in llm_chunks:
        # Fit chunk text to per-chunk token budget before estimating batch cost
        fitted_text = fit_chunk_text(chunk["text"], _MAX_CHUNK_TOKENS)
        chunk_tokens = estimate_tokens(fitted_text) + 60  # +60 for metadata fields
        eid = chunk["evidence_id"]
        crosses_evidence = bool(current) and current_eid != eid
        exceeds_budget = bool(current) and current_tokens + chunk_tokens > available

        if crosses_evidence or exceeds_budget:
            if current:
                batches.append(current)
            current, current_tokens = [], 0

        current.append({**chunk, "_fitted_text": fitted_text})
        current_tokens += chunk_tokens
        current_eid = eid

    if current:
        batches.append(current)
    return batches or [[]]


def _output_budget_for_batch(batch: list[dict], budget: object) -> int:
    """Compute output token budget for this specific batch."""
    needed = _LLM_OUTPUT_ENVELOPE + len(batch) * _PER_CHUNK_OUTPUT_ESTIMATE
    return min(needed, budget.output_reserve)  # type: ignore[attr-defined]


def _estimate_batch_input_tokens(batch: list[dict]) -> int:
    return sum(estimate_tokens(item.get("_fitted_text", item["text"])) + 60 for item in batch)


def _batch_payload_chars(batch: list[dict]) -> int:
    return sum(len(item.get("_fitted_text", item["text"])) for item in batch)


# ── LLM call with retry/telemetry ────────────────────────────────────────────

async def _analyse_batch_with_retries(
    batch: list[dict],
    batch_number: int = 1,
    batch_total: int = 1,
) -> dict:
    """Call the LLM for a batch with structured retry, reduction, and telemetry.

    Error handling:
      • ContextTooLargeError (413): split batch in half, retry each half
      • 400 JSON validation: reduce by 1 chunk, recompute output budget, retry once
      • 429 rate limit: exponential backoff
      • All failures: emit structured telemetry before any retry/fallback
    """
    budget = get_token_budget(output_reserve=_output_budget_for_batch(batch, get_token_budget()))
    chunk_payload = _build_chunk_payload(batch)
    user_prompt = (
        "Analyze each evidence chunk separately. Return JSON with a top-level "
        "`chunks` array. Each item must include ci (chunk_index) and the "
        "short-key extraction fields.\n\n"
        f"Evidence chunks:\n{json.dumps(chunk_payload, separators=(',', ':'))}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # Pre-call validation
    max_out = _output_budget_for_batch(batch, budget)
    validation = validate_payload(messages, max_output_tokens=max_out, budget=budget)
    if not validation.fits and len(batch) > 1:
        # Split into two halves and recurse
        mid = len(batch) // 2
        result_a = await _analyse_batch_with_retries(batch[:mid], batch_number, batch_total)
        result_b = await _analyse_batch_with_retries(batch[mid:], batch_number, batch_total)
        merged_chunks = (result_a.get("chunks") or []) + (result_b.get("chunks") or [])
        return {"chunks": merged_chunks}

    evidence_ids = list({item["evidence_id"] for item in batch})
    chunk_indices = [f"{item['evidence_id']}:{item['chunk_index']}" for item in batch]
    estimated_input = validation.estimated_input_tokens

    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result = await chat_completion_json(messages, temperature=0.1, max_tokens=max_out)
            return result if isinstance(result, dict) else {}

        except ContextTooLargeError as exc:
            emit_llm_failure_telemetry(
                agent_name="evidence_analyzer",
                error_code=413,
                error_category="context_too_large",
                estimated_input_tokens=estimated_input,
                requested_output_tokens=max_out,
                payload_size_chars=len(user_prompt),
                attempt_number=attempt,
                batch_number=batch_number,
                batch_total=batch_total,
                evidence_ids=evidence_ids,
                chunk_indices=chunk_indices,
                was_retried=len(batch) > 1,
                retry_action="split_batch" if len(batch) > 1 else None,
                fallback_action=None if len(batch) > 1 else "deterministic_extraction",
                original_message=exc.original_message,
            )
            if len(batch) > 1:
                mid = len(batch) // 2
                result_a = await _analyse_batch_with_retries(batch[:mid], batch_number, batch_total)
                result_b = await _analyse_batch_with_retries(batch[mid:], batch_number, batch_total)
                return {"chunks": (result_a.get("chunks") or []) + (result_b.get("chunks") or [])}
            raise

        except ValueError as exc:
            # 400 JSON validation: reduce batch by 1 chunk, recompute output budget
            emit_llm_failure_telemetry(
                agent_name="evidence_analyzer",
                error_code=400,
                error_category="json_validation",
                estimated_input_tokens=estimated_input,
                requested_output_tokens=max_out,
                payload_size_chars=len(user_prompt),
                attempt_number=attempt,
                batch_number=batch_number,
                batch_total=batch_total,
                evidence_ids=evidence_ids,
                chunk_indices=chunk_indices,
                was_retried=len(batch) > 1,
                retry_action="reduce_batch_size" if len(batch) > 1 else None,
                fallback_action=None,
                original_message=str(exc),
            )
            last_error = exc
            if len(batch) > 1 and attempt < _MAX_RETRIES:
                # Shrink batch by 1 and rebuild prompt with smaller output budget
                batch = batch[:-1]
                max_out = _output_budget_for_batch(batch, budget)
                chunk_payload = _build_chunk_payload(batch)
                user_prompt = (
                    "Analyze each evidence chunk separately. Return JSON with a top-level "
                    "`chunks` array. Each item must include ci (chunk_index) and the "
                    "short-key extraction fields.\n\n"
                    f"Evidence chunks:\n{json.dumps(chunk_payload, separators=(',', ':'))}"
                )
                messages = [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
                estimated_input = estimate_tokens(user_prompt)
                continue
            break

        except Exception as exc:
            last_error = exc
            code, category = _classify_llm_error(exc)
            is_rate_limit = _is_rate_limit_error(exc)
            emit_llm_failure_telemetry(
                agent_name="evidence_analyzer",
                error_code=code,
                error_category=category,
                estimated_input_tokens=estimated_input,
                requested_output_tokens=max_out,
                payload_size_chars=len(user_prompt),
                attempt_number=attempt,
                batch_number=batch_number,
                batch_total=batch_total,
                evidence_ids=evidence_ids,
                chunk_indices=chunk_indices,
                was_retried=is_rate_limit and attempt < _MAX_RETRIES,
                retry_action="exponential_backoff" if is_rate_limit else None,
                fallback_action=None,
                original_message=str(exc),
            )
            if is_rate_limit and attempt < _MAX_RETRIES:
                await asyncio.sleep(5 * attempt)
                continue
            break

    raise last_error or RuntimeError("Unknown analysis error")


def _build_chunk_payload(batch: list[dict]) -> list[dict]:
    """Build the compact payload sent to the LLM for a batch."""
    return [
        {
            "evidence_id": item["evidence_id"],
            "filename": item["filename"],
            "ci": item["chunk_index"],
            "text": item.get("_fitted_text", item["text"]),
        }
        for item in batch
    ]


def _classify_llm_error(exc: Exception) -> tuple[int | str, str]:
    msg = str(exc).lower()
    if isinstance(exc, ContextTooLargeError) or "413" in msg:
        return 413, "context_too_large"
    if "400" in msg or "json" in msg:
        return 400, "json_validation"
    if "429" in msg or "rate limit" in msg:
        return 429, "rate_limit"
    return "unknown", "unknown"


# ── Result parsing ────────────────────────────────────────────────────────────

def _analysis_items_from_result(result: dict, batch: list[dict]) -> list[tuple[dict, dict]]:
    """Parse LLM result into (chunk_item, entities) pairs, expanding short keys."""
    items = result.get("chunks") if isinstance(result, dict) else None
    if not isinstance(items, list):
        return [(item, result if isinstance(result, dict) else {}) for item in batch]

    batch_by_index = {int(item["chunk_index"]): item for item in batch}
    analysed: list[tuple[dict, dict]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        # Expand short keys → full keys
        expanded = _expand_short_keys(raw)
        try:
            chunk_index = int(expanded.get("chunk_index", raw.get("ci", -1)))
        except (TypeError, ValueError):
            continue
        item = batch_by_index.get(chunk_index)
        if not item:
            continue
        entities = {key: expanded.get(key, []) for key in _ENTITY_KEYS}
        # INVARIANT: key_findings must be a list; clean any non-list values
        if not isinstance(entities.get("key_findings"), list):
            entities["key_findings"] = []
        analysed.append((item, entities))

    seen = {item["chunk_index"] for item, _ in analysed}
    for item in batch:
        if item["chunk_index"] not in seen:
            analysed.append((item, _empty_entities()))
    return analysed


def _expand_short_keys(raw: dict) -> dict:
    """Convert short-key LLM response fields to full field names."""
    expanded = {}
    for k, v in raw.items():
        full_key = _SHORT_KEY_MAP.get(k, k)
        expanded[full_key] = v
    return expanded


# ── Entity application ────────────────────────────────────────────────────────

def _apply_entities(output: dict, chunk: dict, entities: dict) -> None:
    """Apply extracted entities from one chunk to the output dict."""
    evidence_id = chunk["evidence_id"]
    filename = chunk["filename"]
    chunk_index = chunk["chunk_index"]
    output["chunks_analyzed"] += 1
    bucket = _ensure_evidence_bucket(output, evidence_id, filename)
    provenance_id = _provenance_id(evidence_id, chunk_index)
    output["provenance_index"][provenance_id] = {
        "evidence_id": evidence_id,
        "filename": filename,
        "chunk_index": chunk_index,
    }

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
            _add_canonical_item(output, bucket, key, cleaned, provenance_id)


# ── Deterministic extraction ──────────────────────────────────────────────────

def _deterministic_extract(text: str) -> dict:
    """Extract entities using regex patterns only.

    INVARIANT: key_findings is ALWAYS [] — findings require LLM semantic
    interpretation. Deterministic extraction produces observations only.
    """
    output: dict = {key: [] for key in _ENTITY_KEYS}
    output["contact_info"] = _dedupe_list([
        *_EMAIL_RE.findall(text),
        *[item.rstrip(".,;") for item in _URL_RE.findall(text)],
        *[_normalise_text(item) for item in _PHONE_RE.findall(text)],
    ])
    output["dates"] = _dedupe_list([*_ISO_DATE_RE.findall(text), *_MONTH_DATE_RE.findall(text)])
    output["times"] = _dedupe_list(_TIME_RE.findall(text))
    output["organisations"] = _dedupe_list(
        [match.group(0).strip(" ,.;") for match in _ORG_SUFFIX_RE.finditer(text)]
    )
    output["identifiers"] = _dedupe_list(
        [match.group(0).upper() for match in _ID_RE.finditer(text)]
    )
    output["amounts"] = _dedupe_list([match.group(0) for match in _AMOUNT_RE.finditer(text)])

    anchors = output["identifiers"] + output["amounts"] + output["contact_info"] + output["dates"]
    if anchors:
        output["key_statements"] = _dedupe_list(_statement_snippets(text, anchors))

    # INVARIANT — enforced explicitly, never conditional:
    output["key_findings"] = []

    return output


def _statement_snippets(text: str, needles: list[str]) -> list[str]:
    snippets = []
    for needle in needles[:12]:
        if not needle:
            continue
        index = text.casefold().find(needle.casefold())
        if index < 0:
            continue
        left = max(0, index - 90)
        right = min(len(text), index + len(needle) + 90)
        snippets.append(_normalise_text(text[left:right]))
    return snippets


# ── Output structure helpers ──────────────────────────────────────────────────

def _empty_output() -> dict:
    return {
        "people": [], "organisations": [], "locations": [], "dates": [], "times": [],
        "contact_info": [], "identifiers": [], "amounts": [],
        "key_statements": [], "key_findings": [],
        "observations": [], "findings": [],
        "entities_by_evidence": {}, "provenance_index": {},
        "referenced_key_statements": [], "referenced_key_findings": [],
        "referenced_observations": [], "failed_chunks": [],
        "chunks_analyzed": 0, "chunks_failed": 0, "llm_fallback_chunks": 0,
        "referenced_entities": [],
    }


def _empty_entities() -> dict:
    return {key: [] for key in _ENTITY_KEYS}


def _normalise_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _batch_label(batch: list[dict]) -> str:
    if not batch:
        return "empty batch"
    evidence_ids = sorted({item["evidence_id"] for item in batch})
    indexes = [str(item["chunk_index"]) for item in batch]
    evidence_label = evidence_ids[0] if len(evidence_ids) == 1 else f"{evidence_ids[0]}-{evidence_ids[-1]}"
    return f"{evidence_label} chunk(s) {', '.join(indexes[:4])}{'...' if len(indexes) > 4 else ''}"


def _add_unique(target: list[str], value: object) -> bool:
    cleaned = _normalise_text(value)
    if not cleaned:
        return False
    existing = {item.casefold() for item in target}
    if cleaned.casefold() in existing:
        return False
    target.append(cleaned)
    return True


def _dedupe_list(values: list[str]) -> list[str]:
    result, seen = [], set()
    for value in values:
        text = _normalise_text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _has_entities(entities: dict) -> bool:
    return any(entities.get(key) for key in _ENTITY_KEYS)


def _ensure_evidence_bucket(output: dict, evidence_id: str, filename: str) -> dict:
    buckets = output["entities_by_evidence"]
    if evidence_id not in buckets:
        buckets[evidence_id] = {
            "filename": filename,
            "people": [], "organisations": [], "locations": [],
            "dates": [], "times": [], "contact_info": [],
            "identifiers": [], "amounts": [],
            "key_statements": [], "key_findings": [],
            "observations": [], "findings": [],
        }
    return buckets[evidence_id]


def _add_reference(
    output: dict, key: str, text: str, evidence_id: str, filename: str, chunk_index: int,
) -> None:
    if key not in {"key_statements", "key_findings"}:
        _add_entity_reference(output, key, text, evidence_id, filename, chunk_index)
        return
    target_key = "referenced_key_statements" if key == "key_statements" else "referenced_key_findings"
    candidate = {"text": text, "evidence_id": evidence_id, "filename": filename, "chunk_index": chunk_index}
    existing = {
        (item.get("text", "").casefold(), item.get("evidence_id", ""), item.get("chunk_index", 0))
        for item in output[target_key]
    }
    identity = (text.casefold(), evidence_id, chunk_index)
    if identity not in existing:
        output[target_key].append(candidate)
    _add_entity_reference(output, key, text, evidence_id, filename, chunk_index)


def _provenance_id(evidence_id: str, chunk_index: int) -> str:
    return f"{evidence_id}:chunk:{chunk_index}"


def _add_canonical_item(output: dict, bucket: dict, field: str, text: str, provenance_id: str) -> None:
    item_type = _canonical_type(field)
    if field == "key_findings":
        target_key = "findings"
        item = {"text": text, "type": "derived_finding", "observation_refs": [provenance_id], "confidence": "medium"}
    else:
        target_key = "observations"
        item = {"text": text, "field": field, "type": item_type, "provenance_id": provenance_id,
                "confidence": "high" if field != "key_statements" else "medium"}
    _add_unique_dict(output[target_key], item, _canonical_identity(target_key, item))
    _add_unique_dict(bucket[target_key], item, _canonical_identity(target_key, item))
    if target_key == "observations":
        _add_unique_dict(output["referenced_observations"], item, _canonical_identity(target_key, item))


def _canonical_type(field: str) -> str:
    return {
        "people": "entity.person", "organisations": "entity.organisation",
        "locations": "entity.location", "dates": "temporal.date", "times": "temporal.time",
        "contact_info": "contact", "identifiers": "identifier", "amounts": "amount",
        "key_statements": "statement",
    }.get(field, "observation")


def _canonical_identity(target_key: str, item: dict) -> tuple:
    if target_key == "findings":
        return (item.get("type", ""), item.get("text", "").casefold(), tuple(item.get("observation_refs", [])))
    return (item.get("field", ""), item.get("text", "").casefold(), item.get("provenance_id", ""))


def _add_unique_dict(target: list[dict], item: dict, identity: tuple) -> bool:
    target_key = "findings" if "observation_refs" in item else "observations"
    existing = {_canonical_identity(target_key, row) for row in target}
    if identity in existing:
        return False
    target.append(item)
    return True


def _add_entity_reference(
    output: dict, key: str, text: str, evidence_id: str, filename: str, chunk_index: int,
) -> None:
    candidate = {"field": key, "text": text, "evidence_id": evidence_id,
                 "filename": filename, "chunk_index": chunk_index}
    identity = (key, text.casefold(), evidence_id, chunk_index)
    existing = {
        (item.get("field", ""), item.get("text", "").casefold(),
         item.get("evidence_id", ""), item.get("chunk_index", 0))
        for item in output["referenced_entities"]
    }
    if identity not in existing:
        output["referenced_entities"].append(candidate)


def _add_failed_chunk(
    output: dict, evidence_id: str, filename: str, chunk_index: int, reason: str,
) -> None:
    output["chunks_failed"] += 1
    output["failed_chunks"].append({
        "evidence_id": evidence_id, "filename": filename,
        "chunk_index": chunk_index, "reason": reason,
    })


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _safe_error(exc: Exception, limit: int = 160) -> str:
    text = str(exc) or exc.__class__.__name__
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer|token)\s*[:=]\s*['\"]?[^'\"\s,}]+", r"\1=[redacted]", text)
    text = re.sub(r"gsk_[A-Za-z0-9_\-]+", "[redacted_groq_key]", text)
    return _normalise_text(text)[:limit]


# ── DB helpers ────────────────────────────────────────────────────────────────

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
    investigation_id: str, agent_id: str, lines: list[dict],
    summary: list[str], output: dict, status: str = "done",
) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {"$set": {"status": status, "summary": summary, "output": output, "updated_at": utcnow()}},
    )
