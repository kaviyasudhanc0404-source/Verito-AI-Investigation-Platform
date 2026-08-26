"""
agents/evidence_linker.py
--------------------------
Agent 04 - Evidence Linker

Finds evidence-to-evidence relationships from deterministic analyzer/timeline
signals first, then lets the LLM synthesize concise labels and classifications.
Every saved link is validated against deterministic support and provenance.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from collections import defaultdict
from typing import Awaitable, Callable

from app.database import collections
from app.services.llm_service import (
    ContextTooLargeError,
    chat_completion_json,
    emit_llm_failure_telemetry,
)
from app.utils.context_budget import (
    estimate_tokens,
    fit_items_by_tokens,
    get_token_budget,
    validate_payload,
)
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

_SYSTEM_PROMPT = """You are a forensic link analyst.
Classify and explain ONLY the provided deterministic link candidates.

Rules:
- Do not invent links, evidence IDs, entities, timestamps, places, or reasons.
- Use only candidate_id values from the provided candidates.
- A link is strong when it has unique/high-value identifiers or multiple support types.
- A link is moderate when it has meaningful corroboration but weaker uniqueness.
- A link is weak when the shared value is broad, common, or only lightly supportive.
- Mention contradiction only when the candidate support explicitly says contradiction.
- Return ONLY valid JSON in the requested schema.

Return JSON:
{
  "links": [
    {
      "candidate_id": "L-001",
      "reason": "brief evidence-grounded explanation",
      "strength": "strong" | "moderate" | "weak",
      "confidence": "high" | "medium" | "low"
    }
  ]
}"""

_ENTITY_FIELDS = (
    "contact_info",
    "identifiers",
    "people",
    "organisations",
    "locations",
    "dates",
    "times",
)
_FIELD_TYPE = {
    "contact_info": "shared_contact",
    "identifiers": "shared_identifier",
    "people": "shared_person",
    "organisations": "shared_organisation",
    "locations": "shared_location",
    "dates": "shared_date",
    "times": "shared_time",
}
_FIELD_LABEL = {
    "contact_info": "contact detail",
    "identifiers": "identifier",
    "people": "person",
    "organisations": "organisation",
    "locations": "location",
    "dates": "date",
    "times": "time",
}
_FIELD_SCORE = {
    "contact_info": 5.0,
    "identifiers": 4.0,
    "people": 3.0,
    "organisations": 3.0,
    "locations": 2.0,
    "dates": 1.5,
    "times": 1.0,
}
_VALID_STRENGTHS = {"strong", "moderate", "weak"}
_VALID_CONFIDENCE = {"high", "medium", "low"}
_MAX_CANDIDATES_FOR_LLM = 40      # Hard cap before token fitting
_SYNTHESIS_OUTPUT_RESERVE = 1600  # Output tokens reserved for synthesis response
_MAX_SHARED_VALUES_PER_SUPPORT = 6
_MAX_PROVENANCE_ITEMS = 20

# Generic terms that must not anchor a link on their own.
# A link whose only support is one of these values is always downgraded.
_GENERIC_STOPLIST = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sir", "madam",
    "unknown", "n/a", "na", "none", "null", "yes", "no",
    "true", "false", "other", "misc", "various",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "00:00", "12:00", "india", "usa", "us", "uk",
})

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^(?:https?://|www\.)", re.IGNORECASE)
_NON_DIGIT_RE = re.compile(r"\D+")


async def run(
    investigation_id: str,
    emit: Emitter,
    analyzer_output: dict,
    timeline_output: dict,
) -> dict:
    """Run the Evidence Linker agent."""
    agent_id = "linker"
    await _set_status(investigation_id, agent_id, "running")
    lines: list[dict] = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    await add_line("scan", "Building deterministic evidence link candidates...")

    evidence_docs = await collections.evidence().find(
        {"investigation_id": investigation_id}
    ).to_list(length=None)
    evidence_ids = {str(ev.get("evidence_id") or "") for ev in evidence_docs}

    candidates = _build_link_candidates(evidence_docs, analyzer_output, timeline_output)
    await add_line(
        "info",
        f"Found {len(candidates)} candidate link(s) with deterministic support.",
    )

    if not candidates:
        output = _empty_output()
        await add_line("warn", "No cross-evidence support was found.")
        await _complete(investigation_id, agent_id, lines, ["No evidence links"], output, status="completed_with_warnings")
        await emit("agent_completed_with_warnings", {"agent_id": agent_id})
        return output

    budget = get_token_budget(output_reserve=_SYNTHESIS_OUTPUT_RESERVE)
    compact_candidates = _compact_candidates_for_llm(candidates, budget=budget)
    evidence_ids_list = list({c["a"] for c in candidates} | {c["b"] for c in candidates})
    estimated_input = estimate_tokens(json.dumps(compact_candidates, separators=(',', ':')))

    await add_line("scan", "Synthesising supported links from candidates...")

    user_content = (
        "Deterministic link candidates:\n"
        f"{json.dumps(compact_candidates, separators=(',', ':'))}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    max_out = _SYNTHESIS_OUTPUT_RESERVE

    synthesis: dict = {}
    try:
        synthesis = await chat_completion_json(messages, temperature=0.1, max_tokens=max_out)

    except ContextTooLargeError as exc:
        emit_llm_failure_telemetry(
            agent_name="evidence_linker",
            error_code=413,
            error_category="context_too_large",
            estimated_input_tokens=estimated_input,
            requested_output_tokens=max_out,
            payload_size_chars=len(user_content),
            attempt_number=1,
            evidence_ids=evidence_ids_list,
            was_retried=True,
            retry_action="reduce_context",
            fallback_action=None,
            original_message=exc.original_message,
        )
        # Drop lowest-score candidates until it fits, then retry once
        reduced = compact_candidates[:max(8, len(compact_candidates) // 2)]
        red_content = (
            "Deterministic link candidates:\n"
            f"{json.dumps(reduced, separators=(',', ':'))}"
        )
        try:
            synthesis = await chat_completion_json(
                [{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user", "content": red_content}],
                temperature=0.1, max_tokens=max_out,
            )
        except Exception as exc2:
            emit_llm_failure_telemetry(
                agent_name="evidence_linker",
                error_code="unknown", error_category="unknown",
                estimated_input_tokens=estimate_tokens(red_content),
                requested_output_tokens=max_out, payload_size_chars=len(red_content),
                attempt_number=2, evidence_ids=evidence_ids_list,
                was_retried=False, retry_action=None,
                fallback_action="fallback_synthesis",
                original_message=str(exc2),
            )
            await add_line("warn", f"LLM link synthesis failed after retry: {str(exc2)[:80]}")
            synthesis = _fallback_synthesis(candidates)

    except ValueError as exc:
        emit_llm_failure_telemetry(
            agent_name="evidence_linker",
            error_code=400, error_category="json_validation",
            estimated_input_tokens=estimated_input,
            requested_output_tokens=max_out, payload_size_chars=len(user_content),
            attempt_number=1, evidence_ids=evidence_ids_list,
            was_retried=True, retry_action="reduce_batch_size",
            fallback_action=None, original_message=str(exc),
        )
        # Reduce to half candidates and retry once
        reduced = compact_candidates[:max(8, len(compact_candidates) // 2)]
        red_content = (
            "Deterministic link candidates:\n"
            f"{json.dumps(reduced, separators=(',', ':'))}"
        )
        try:
            synthesis = await chat_completion_json(
                [{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user", "content": red_content}],
                temperature=0.1, max_tokens=max_out,
            )
        except Exception as exc2:
            emit_llm_failure_telemetry(
                agent_name="evidence_linker",
                error_code="unknown", error_category="unknown",
                estimated_input_tokens=estimate_tokens(red_content),
                requested_output_tokens=max_out, payload_size_chars=len(red_content),
                attempt_number=2, evidence_ids=evidence_ids_list,
                was_retried=False, retry_action=None,
                fallback_action="fallback_synthesis", original_message=str(exc2),
            )
            await add_line("warn", f"LLM link synthesis failed after retry: {str(exc2)[:80]}")
            synthesis = _fallback_synthesis(candidates)

    except Exception as exc:
        emit_llm_failure_telemetry(
            agent_name="evidence_linker",
            error_code="unknown", error_category="unknown",
            estimated_input_tokens=estimated_input,
            requested_output_tokens=max_out, payload_size_chars=len(user_content),
            attempt_number=1, evidence_ids=evidence_ids_list,
            was_retried=False, retry_action=None,
            fallback_action="fallback_synthesis", original_message=str(exc),
        )
        await add_line("warn", f"LLM link synthesis failed: {str(exc)[:80]}")
        synthesis = _fallback_synthesis(candidates)

    links = _validate_links(
        synthesis.get("links", []),
        candidates=candidates,
        valid_evidence_ids=evidence_ids,
    )
    output = _build_output(links, candidates)

    await asyncio.sleep(0.2)

    for link in links[:12]:
        line_kind = "ok" if link["strength"] != "weak" else "warn"
        await add_line(
            line_kind,
            (
                f"{link['a']} <-> {link['b']}: {link['reason']} "
                f"[{link['strength']}]"
            ),
        )

    if len(links) > 12:
        await add_line("info", f"{len(links) - 12} additional link(s) saved.")

    await add_line(
        "ok",
        (
            f"{len(links)} validated link(s): "
            f"{output['strong_links']} strong, "
            f"{output['moderate_links']} moderate, "
            f"{output['weak_links']} weak."
        ),
    )

    summary = [
        f"{output['strong_links']} strong link(s)",
        f"{output['moderate_links']} moderate link(s)",
        f"{output['weak_links']} weak link(s)",
    ]

    status = "completed_with_warnings" if output["supported_candidate_count"] == 0 else "done"
    await _complete(investigation_id, agent_id, lines, summary, output, status=status)
    await emit(
        "agent_completed_with_warnings" if status == "completed_with_warnings" else "agent_completed",
        {"agent_id": agent_id},
    )
    return output


def _empty_output() -> dict:
    return {
        "links": [],
        "strong_links": 0,
        "moderate_links": 0,
        "weak_links": 0,
        "candidate_count": 0,
        "supported_candidate_count": 0,
        "link_types": {},
        "link_candidates": [],
    }


def _build_link_candidates(
    evidence_docs: list[dict],
    analyzer_output: dict,
    timeline_output: dict,
) -> list[dict]:
    filenames = {
        str(ev.get("evidence_id") or ""): str(ev.get("filename") or "unknown")
        for ev in evidence_docs
        if ev.get("evidence_id")
    }
    buckets = analyzer_output.get("entities_by_evidence", {})
    if not isinstance(buckets, dict):
        buckets = {}

    pair_supports: dict[tuple[str, str], list[dict]] = defaultdict(list)
    value_frequencies = _build_value_frequencies(buckets)

    for field in _ENTITY_FIELDS:
        index = _build_entity_index(buckets, field)
        for normalised, evidence_values in index.items():
            evidence_list = sorted(evidence_values)
            if len(evidence_list) < 2:
                continue

            uniqueness = len(evidence_list)
            for a, b in itertools.combinations(evidence_list, 2):
                originals_a = evidence_values[a]
                originals_b = evidence_values[b]
                shared_values = _shared_display_values(originals_a, originals_b)
                support = _entity_support(
                    field=field,
                    normalised=normalised,
                    shared_values=shared_values,
                    a=a,
                    b=b,
                    filenames=filenames,
                    uniqueness=uniqueness,
                    frequency=value_frequencies.get((field, normalised), uniqueness),
                )
                pair_supports[_pair_key(a, b)].append(support)

    for support in _timeline_supports(timeline_output, filenames):
        pair_supports[_pair_key(support["a"], support["b"])].append(support)

    candidates = []
    for index, (pair, supports) in enumerate(sorted(pair_supports.items()), start=1):
        supports = _dedupe_supports(supports)
        score = sum(float(s.get("score", 0)) for s in supports)
        candidates.append(
            _candidate_from_supports(
                candidate_id=f"L-{index:03d}",
                pair=pair,
                supports=supports,
                score=score,
            )
        )

    return sorted(candidates, key=lambda c: (-c["score"], c["a"], c["b"]))


def _build_value_frequencies(buckets: dict) -> dict[tuple[str, str], int]:
    frequencies: dict[tuple[str, str], set[str]] = defaultdict(set)
    for evidence_id, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        for current_field in _ENTITY_FIELDS:
            for value in _clean_list(bucket.get(current_field, [])):
                normalised = _normalise_entity(current_field, value)
                if normalised:
                    frequencies[(current_field, normalised)].add(str(evidence_id))
    return {key: len(ids) for key, ids in frequencies.items()}


def _build_entity_index(buckets: dict, field: str) -> dict[str, dict[str, list[str]]]:
    index: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for evidence_id, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue

        for value in _clean_list(bucket.get(field, [])):
            normalised = _normalise_entity(field, value)
            if not normalised:
                continue
            target = index[normalised][str(evidence_id)]
            if value not in target:
                target.append(value)

    return index


def _entity_support(
    *,
    field: str,
    normalised: str,
    shared_values: list[str],
    a: str,
    b: str,
    filenames: dict[str, str],
    uniqueness: int,
    frequency: int,
) -> dict:
    uniqueness_bonus = 1.5 if frequency == 2 else 0.75 if frequency <= 4 else 0.0
    score = _FIELD_SCORE[field] + uniqueness_bonus
    if field in {"dates", "times"} and frequency > 4:
        score *= 0.5

    label = _FIELD_LABEL[field]
    return {
        "type": _FIELD_TYPE[field],
        "field": field,
        "a": a,
        "b": b,
        "shared_values": shared_values[:_MAX_SHARED_VALUES_PER_SUPPORT],
        "normalised_value": normalised,
        "reason_hint": (
            f"Both evidence items share {label}: "
            f"{', '.join(shared_values[:3]) or normalised}."
        ),
        "score": round(score, 2),
        "uniqueness": uniqueness,
        "provenance": [
            {
                "evidence_id": a,
                "filename": filenames.get(a, "unknown"),
                "field": field,
                "value": shared_values[0] if shared_values else normalised,
            },
            {
                "evidence_id": b,
                "filename": filenames.get(b, "unknown"),
                "field": field,
                "value": shared_values[-1] if shared_values else normalised,
            },
        ],
    }


def _timeline_supports(timeline_output: dict, filenames: dict[str, str]) -> list[dict]:
    events = timeline_output.get("events", [])
    if not isinstance(events, list):
        return []

    supports = []
    for event in events:
        if not isinstance(event, dict):
            continue

        refs = sorted(set(_clean_list(event.get("refs", []))))
        if len(refs) < 2:
            continue

        sort_time = _normalise_text(event.get("sort_time"))
        event_text = _normalise_text(event.get("event", "")) or "same timeline event"
        classification = (_normalise_text(event.get("classification")) or "FACT").upper()
        confidence = (_normalise_text(event.get("confidence")) or "medium").lower()
        score = 3.0 if sort_time else 2.0
        if classification == "FACT":
            score += 0.5
        if confidence == "high":
            score += 0.5

        for a, b in itertools.combinations(refs, 2):
            supports.append(
                {
                    "type": "temporal_overlap",
                    "field": "timeline",
                    "a": a,
                    "b": b,
                    "shared_values": [sort_time or event.get("time") or "timeline event"],
                    "normalised_value": sort_time or event_text.casefold(),
                    "reason_hint": f"Both evidence items support timeline event: {event_text}.",
                    "score": round(score, 2),
                    "uniqueness": len(refs),
                    "provenance": [
                        {
                            "evidence_id": a,
                            "filename": filenames.get(a, "unknown"),
                            "field": "timeline",
                            "value": event_text,
                            "sort_time": sort_time or None,
                        },
                        {
                            "evidence_id": b,
                            "filename": filenames.get(b, "unknown"),
                            "field": "timeline",
                            "value": event_text,
                            "sort_time": sort_time or None,
                        },
                    ],
                }
            )

    return supports


def _candidate_from_supports(
    *,
    candidate_id: str,
    pair: tuple[str, str],
    supports: list[dict],
    score: float,
) -> dict:
    types = sorted({support["type"] for support in supports})
    provenance = []
    for support in supports:
        provenance.extend(support.get("provenance", []))

    return {
        "candidate_id": candidate_id,
        "a": pair[0],
        "b": pair[1],
        "types": types,
        "support_count": len(supports),
        "score": round(score, 2),
        "suggested_strength": _strength_from_score(score, supports),
        "suggested_confidence": _confidence_from_score(score, supports),
        "reason_hint": _reason_hint(supports),
        "supports": supports,
        "provenance": _dedupe_provenance(provenance)[:_MAX_PROVENANCE_ITEMS],
    }


def _compact_candidates_for_llm(candidates: list[dict], *, budget: object | None = None) -> list[dict]:
    """Build a minimal, token-safe candidate list for LLM synthesis.

    Strips each candidate to only the 4 fields the LLM needs:
      candidate_id, a, b, reason_hint
    Generic-only candidates are filtered out before sending.
    Hard-capped at _MAX_CANDIDATES_FOR_LLM before token fitting.
    """
    from app.utils.context_budget import get_token_budget as _get_budget
    if budget is None:
        budget = _get_budget(output_reserve=_SYNTHESIS_OUTPUT_RESERVE)

    # Filter candidates whose only support values are generic terms
    non_generic = [c for c in candidates if not _is_generic_only_candidate(c)]
    if not non_generic:
        non_generic = candidates  # Fallback: keep all if all are generic

    # Sort by score descending, take top _MAX_CANDIDATES_FOR_LLM
    top = sorted(non_generic, key=lambda c: -c["score"])[:_MAX_CANDIDATES_FOR_LLM]

    # Strip each candidate to the minimum 4 fields the LLM needs
    compact = [
        {
            "candidate_id": c["candidate_id"],
            "a": c["a"],
            "b": c["b"],
            "reason_hint": c["reason_hint"],
        }
        for c in top
    ]

    # Token-safe fitting: never exceed effective_input budget
    system_tokens = estimate_tokens(_SYSTEM_PROMPT)
    available = budget.effective_input - system_tokens - 60  # type: ignore[attr-defined]
    fitted = fit_items_by_tokens(
        compact,
        budget_tokens=max(available, 500),
        min_items=min(8, len(compact)),
    )
    return fitted


def _is_generic_only_candidate(candidate: dict) -> bool:
    """Return True if all shared values for this candidate are generic terms.

    A candidate passes through if ANY support value is NOT in the stoplist.
    Only filter when every shared value across all supports is generic.
    """
    all_values: list[str] = []
    for support in candidate.get("supports", []):
        all_values.extend(v.strip().lower() for v in support.get("shared_values", []))
    if not all_values:
        return False  # No shared values — cannot judge, keep
    return all(v in _GENERIC_STOPLIST for v in all_values)


def _fallback_synthesis(candidates: list[dict]) -> dict:
    return {
        "links": [
            {
                "candidate_id": candidate["candidate_id"],
                "reason": candidate["reason_hint"],
                "strength": candidate["suggested_strength"],
                "confidence": candidate["suggested_confidence"],
            }
            for candidate in candidates[:_MAX_CANDIDATES_FOR_LLM]
        ]
    }


def _validate_links(
    links: object,
    *,
    candidates: list[dict],
    valid_evidence_ids: set[str],
    allow_fallback: bool = True,
) -> list[dict]:
    if not isinstance(links, list):
        links = []

    candidate_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    cleaned_links = []
    seen_pairs: set[tuple[str, str]] = set()

    for item in links:
        if not isinstance(item, dict):
            continue

        candidate = candidate_by_id.get(str(item.get("candidate_id") or ""))
        if not candidate:
            continue

        a = candidate["a"]
        b = candidate["b"]
        if a == b or a not in valid_evidence_ids or b not in valid_evidence_ids:
            continue
        if not candidate.get("supports") or not candidate.get("provenance"):
            continue

        pair = _pair_key(a, b)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        strength = str(item.get("strength") or candidate["suggested_strength"]).lower()
        if strength not in _VALID_STRENGTHS:
            strength = candidate["suggested_strength"]
        strength = _cap_strength_by_support(strength, candidate)

        confidence = str(item.get("confidence") or candidate["suggested_confidence"]).lower()
        if confidence not in _VALID_CONFIDENCE:
            confidence = candidate["suggested_confidence"]

        reason = _normalise_text(item.get("reason")) or candidate["reason_hint"]
        cleaned_links.append(
            {
                "a": a,
                "b": b,
                "reason": reason,
                "strength": strength,
                "confidence": confidence,
                "types": candidate["types"],
                "score": candidate["score"],
                "support_count": candidate["support_count"],
                "supports": candidate["supports"],
                "provenance": candidate["provenance"],
                "candidate_id": candidate["candidate_id"],
            }
        )

    if cleaned_links or not allow_fallback:
        return sorted(cleaned_links, key=lambda link: (-link["score"], link["a"], link["b"]))

    return _validate_links(
        _fallback_synthesis(candidates).get("links", []),
        candidates=candidates,
        valid_evidence_ids=valid_evidence_ids,
        allow_fallback=False,
    )


def _build_output(links: list[dict], candidates: list[dict]) -> dict:
    type_counts: dict[str, int] = defaultdict(int)
    for link in links:
        for link_type in link.get("types", []):
            type_counts[link_type] += 1

    return {
        "links": links,
        "strong_links": sum(1 for link in links if link["strength"] == "strong"),
        "moderate_links": sum(1 for link in links if link["strength"] == "moderate"),
        "weak_links": sum(1 for link in links if link["strength"] == "weak"),
        "candidate_count": len(candidates),
        "supported_candidate_count": len(links),
        "link_types": dict(sorted(type_counts.items())),
        "link_candidates": _compact_candidates_for_llm(candidates),
    }


def _normalise_entity(field: str, value: object) -> str:
    text = _normalise_text(value)
    if not text:
        return ""

    if field == "contact_info":
        return _normalise_contact(text)

    if field == "identifiers":
        return re.sub(r"[^a-z0-9-]", "", text.casefold())

    if field in {"people", "organisations", "locations"}:
        return re.sub(r"[^\w\s&.-]", "", text.casefold()).strip()

    return text.casefold()


def _normalise_contact(value: str) -> str:
    text = value.strip().casefold()
    if _EMAIL_RE.match(text):
        return text

    if _URL_RE.match(text):
        return text.removeprefix("http://").removeprefix("https://").rstrip("/")

    digits = _NON_DIGIT_RE.sub("", text)
    if 7 <= len(digits) <= 15:
        return digits[-10:] if len(digits) > 10 else digits

    return re.sub(r"\s+", "", text)


def _shared_display_values(values_a: list[str], values_b: list[str]) -> list[str]:
    combined = values_a + values_b
    cleaned = []
    seen = set()
    for value in combined:
        text = _normalise_text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned


def _strength_from_score(score: float, supports: list[dict]) -> str:
    support_types = {support["type"] for support in supports}
    has_contact = "shared_contact" in support_types
    has_identifier = "shared_identifier" in support_types
    has_temporal = "temporal_overlap" in support_types
    has_location = "shared_location" in support_types

    if score >= 7.5 or (has_contact and (len(support_types) > 1 or has_temporal)):
        return "strong"
    if has_identifier and len(support_types) > 1:
        return "strong"
    if score >= 4.0 or (has_location and has_temporal):
        return "moderate"
    return "weak"


def _confidence_from_score(score: float, supports: list[dict]) -> str:
    if score >= 7.5 or len(supports) >= 3:
        return "high"
    if score >= 4.0 or len(supports) >= 2:
        return "medium"
    return "low"


def _cap_strength_by_support(strength: str, candidate: dict) -> str:
    suggested = candidate["suggested_strength"]
    order = {"weak": 0, "moderate": 1, "strong": 2}
    if order[strength] > order[suggested]:
        return suggested
    return strength


def _reason_hint(supports: list[dict]) -> str:
    if not supports:
        return "Evidence items share deterministic support."

    ordered = sorted(supports, key=lambda s: -float(s.get("score", 0)))
    hints = [support["reason_hint"] for support in ordered[:3]]
    return " ".join(hints)


def _dedupe_supports(supports: list[dict]) -> list[dict]:
    unique = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for support in supports:
        key = (
            support.get("type", ""),
            support.get("normalised_value", ""),
            tuple(sorted(str(v).casefold() for v in support.get("shared_values", []))),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(support)
    return unique


def _dedupe_provenance(items: list[dict]) -> list[dict]:
    unique = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        identity = (
            str(item.get("evidence_id", "")),
            str(item.get("field", "")),
            str(item.get("value", "")).casefold(),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def _clean_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []

    cleaned = []
    seen = set()
    for value in values:
        text = _normalise_text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned


def _normalise_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _pair_key(a: str, b: str) -> tuple[str, str]:
    first, second = sorted((str(a), str(b)))
    return first, second


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
    status: str = "done",
) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {
            "$set": {
                "status": status,
                "summary": summary,
                "output": output,
                "updated_at": utcnow(),
            }
        },
    )
