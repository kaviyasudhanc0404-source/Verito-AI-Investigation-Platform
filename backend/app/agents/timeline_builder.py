"""
agents/timeline_builder.py
---------------------------
Agent 03 - Timeline Builder

Builds a chronological event timeline from structured analyzer output and
compact evidence snippets. The LLM is used for synthesis, while Python handles
temporal extraction, provenance, ambiguity checks, validation, and sorting.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
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

_SYSTEM_PROMPT = """You are a forensic timeline analyst.
Build a chronological timeline using ONLY the provided timeline candidates.

Rules:
- Do not invent dates, times, events, people, places, or evidence references.
- Do not resolve ambiguous dates. Keep them as ambiguous/UNCONFIRMED.
- Merge duplicate candidates only when they clearly describe the same event.
- Prefer direct evidence-backed facts over inference.
- Every event must include at least one evidence reference from the candidates.
- Return ONLY valid JSON in the requested schema.

Return JSON:
{
  "events": [
    {
      "time": "readable date/time string",
      "sort_time": "ISO 8601 timestamp or null",
      "event": "brief evidence-grounded event description",
      "refs": ["E-001"],
      "classification": "FACT" | "INFERRED" | "UNCONFIRMED",
      "confidence": "high" | "medium" | "low"
    }
  ],
  "time_window": "readable range or 'Unknown'"
}"""

_VALID_CLASSIFICATIONS = {"FACT", "INFERRED", "UNCONFIRMED"}
_VALID_CONFIDENCE = {"high", "medium", "low"}
_DATE_TYPES = {"EVENT", "DOCUMENT", "REFERENCE", "METADATA", "AMBIGUOUS"}
# No hard candidate cap — batched synthesis handles any count
_MAX_TEXT_SNIPPET_CHARS = 280
_SYNTHESIS_OUTPUT_RESERVE = 2000
_ENVELOPE_TOKENS = 60

_ISO_DATE_RE = re.compile(
    r"\b(?P<year>20\d{2}|19\d{2})-(?P<month>0?[1-9]|1[0-2])-(?P<day>0?[1-9]|[12]\d|3[01])\b"
)
_SLASH_DATE_RE = re.compile(
    r"\b(?P<a>0?[1-9]|[12]\d|3[01])/(?P<b>0?[1-9]|1[0-2])/(?P<year>20\d{2}|19\d{2})\b"
)
_MONTH_DATE_RE = re.compile(
    r"\b(?:(?P<day>0?[1-9]|[12]\d|3[01])\s+(?P<month_name>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)|(?P<month_name_first>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(?P<day_after>0?[1-9]|[12]\d|3[01]),?)\s+(?P<year>20\d{2}|19\d{2})\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)(?::(?P<second>[0-5]\d))?\s*(?P<ampm>AM|PM|am|pm)?(?P<tz>Z|[+-]\d{2}:?\d{2})?\b"
)

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


async def run(investigation_id: str, emit: Emitter, analyzer_output: dict) -> dict:
    """Run the Timeline Builder agent."""
    agent_id = "timeline"
    await _set_status(investigation_id, agent_id, "running")
    lines: list[dict] = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    await add_line("info", "Constructing structured timeline candidates...")

    evidence_docs = await collections.evidence().find(
        {"investigation_id": investigation_id}
    ).to_list(length=None)
    evidence_ids = {str(ev.get("evidence_id", "")) for ev in evidence_docs}

    candidates = _build_timeline_candidates(evidence_docs, analyzer_output)
    ambiguous_count = sum(1 for item in candidates if item.get("ambiguous"))

    await add_line(
        "scan",
        f"Extracted {len(candidates)} temporal candidate(s) with provenance.",
    )
    if ambiguous_count:
        await add_line(
            "warn",
            f"Flagged {ambiguous_count} ambiguous date candidate(s) without guessing.",
        )

    if not candidates:
        output = _empty_output()
        await add_line("warn", "No temporal anchors found in the evidence.")
        await _complete(investigation_id, agent_id, lines, ["No timeline events"], output, status="completed_with_warnings")
        await emit("agent_completed_with_warnings", {"agent_id": agent_id})
        return output

    await add_line("scan", "Synthesising timeline from candidates (batch synthesis)...")

    # Batch synthesis: split candidates into token-safe batches, merge results.
    # Never discards candidates merely because there are many — batching handles them.
    budget = get_token_budget(output_reserve=_SYNTHESIS_OUTPUT_RESERVE)
    compact_all = _compact_candidates_for_llm(candidates)
    batches = _split_candidate_batches(compact_all, budget=budget)
    await add_line("info", f"Timeline synthesis: {len(compact_all)} compact candidates → {len(batches)} batch(es).")

    all_events: list[dict] = []
    synthesis_failed = False
    for batch_num, batch in enumerate(batches, start=1):
        try:
            user_content = (
                "Timeline candidates with provenance:\n"
                f"{json.dumps(batch, separators=(',', ':'))}"
            )
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]
            max_out = _SYNTHESIS_OUTPUT_RESERVE
            validation = validate_payload(messages, max_output_tokens=max_out, budget=budget)
            if not validation.fits:
                # Split batch further if still too large
                sub_batches = _split_candidate_batches(batch, budget=budget)
                for sub in sub_batches:
                    sub_content = (
                        "Timeline candidates with provenance:\n"
                        f"{json.dumps(sub, separators=(',', ':'))}"
                    )
                    sub_msgs = [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": sub_content},
                    ]
                    sub_result = await chat_completion_json(sub_msgs, temperature=0.1, max_tokens=max_out)
                    all_events.extend(sub_result.get("events", []) if isinstance(sub_result, dict) else [])
                continue

            result = await chat_completion_json(messages, temperature=0.1, max_tokens=max_out)
            all_events.extend(result.get("events", []) if isinstance(result, dict) else [])

        except (ContextTooLargeError, Exception) as exc:
            is_413 = isinstance(exc, ContextTooLargeError)
            code = 413 if is_413 else (400 if "400" in str(exc) or "json" in str(exc).lower() else "unknown")
            category = "context_too_large" if is_413 else ("json_validation" if code == 400 else "unknown")
            estimated_input = estimate_tokens(json.dumps(batch, separators=(',', ':')))
            emit_llm_failure_telemetry(
                agent_name="timeline_builder",
                error_code=code,
                error_category=category,
                estimated_input_tokens=estimated_input,
                requested_output_tokens=_SYNTHESIS_OUTPUT_RESERVE,
                payload_size_chars=len(json.dumps(batch, separators=(',', ':'))),
                attempt_number=1,
                batch_number=batch_num,
                batch_total=len(batches),
                evidence_ids=list({c.get("evidence_id", "") for c in batch}),
                was_retried=False,
                retry_action=None,
                fallback_action="deterministic_event_extraction",
                original_message=getattr(exc, 'original_message', str(exc)),
            )
            synthesis_failed = True
            await add_line("warn", f"Timeline batch {batch_num} synthesis failed — using fallback.")
            fallback = _fallback_timeline([c for c in candidates if c.get("evidence_id") in
                                           {b.get("evidence_id") for b in batch}])
            all_events.extend(fallback.get("events", []))

    events = _validate_and_sort_events(
        all_events,
        candidates=candidates,
        valid_evidence_ids=evidence_ids,
    )
    time_window = _compute_time_window(events)
    output = _build_output(events, time_window, candidates)

    await asyncio.sleep(0.2)

    for event in events[:12]:
        classification = event.get("classification", "FACT")
        refs_str = ", ".join(event.get("refs", []))
        await add_line(
            "ok" if classification == "FACT" else "scan",
            (
                f"{event.get('time', '?')}: {event.get('event', '')} "
                f"[{classification}]"
                + (f" ({refs_str})" if refs_str else "")
            ),
        )

    if len(events) > 12:
        await add_line("info", f"{len(events) - 12} additional event(s) saved.")

    await add_line("ok", f"{len(events)} timeline event(s) validated and sorted.")

    summary = [
        f"{len(events)} events sequenced",
        f"Window: {time_window}" if time_window != "Unknown" else "Timeline window unknown",
    ]

    status = "completed_with_warnings" if synthesis_failed else "done"
    await _complete(investigation_id, agent_id, lines, summary, output, status=status)
    await emit(
        "agent_completed_with_warnings" if status == "completed_with_warnings" else "agent_completed",
        {"agent_id": agent_id},
    )
    return output


def _empty_output() -> dict:
    return {
        "events": [],
        "time_window": "Unknown",
        "event_count": 0,
        "candidate_count": 0,
        "ambiguous_candidate_count": 0,
        "fact_count": 0,
        "inferred_count": 0,
        "unconfirmed_count": 0,
        "temporal_candidates": [],
    }


def _build_timeline_candidates(evidence_docs: list[dict], analyzer_output: dict) -> list[dict]:
    entities_by_evidence = analyzer_output.get("entities_by_evidence", {})
    candidates: list[dict] = []
    seen: set[tuple] = set()

    for ev in evidence_docs:
        evidence_id = str(ev.get("evidence_id") or "")
        if not evidence_id:
            continue

        filename = str(ev.get("filename") or "unknown")
        content = _normalise_text(ev.get("content", ""))
        bucket = entities_by_evidence.get(evidence_id, {})

        analyzer_date_mentions = _mentions_from_analyzer(bucket.get("dates", []), "dates")
        regex_date_mentions = _extract_date_mentions(content)
        date_mentions = _merge_date_mentions(analyzer_date_mentions, regex_date_mentions)
        time_mentions = _extract_time_mentions(content)
        if not time_mentions:
            time_mentions = _mentions_from_analyzer(bucket.get("times", []), "times")

        date_mentions = _dedupe_mentions(date_mentions)
        time_mentions = _dedupe_mentions(time_mentions)
        statements = _clean_list(bucket.get("key_statements", []))[:3]
        findings = _clean_list(bucket.get("key_findings", []))[:3]
        snippet = _snippet(content)

        for date_mention in date_mentions[:8]:
            times = _matching_time_mentions(date_mention, time_mentions) or [None]
            for time_mention in times:
                candidate = _candidate_from_mentions(
                    evidence_id=evidence_id,
                    filename=filename,
                    date_mention=date_mention,
                    time_mention=time_mention,
                    statements=statements,
                    findings=findings,
                    snippet=snippet,
                )
                identity = (
                    candidate["evidence_id"],
                    candidate["date_text"].casefold(),
                    candidate.get("time_text", "").casefold(),
                    candidate.get("sort_time"),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append(candidate)

    return candidates


def _mentions_from_analyzer(values: object, field: str) -> list[dict]:
    mentions = []
    for value in _clean_list(values):
        mentions.append(
            {
                "text": value,
                "source": "analyzer",
                "field": field,
                "excerpt": value,
            }
        )
    return mentions


def _merge_date_mentions(analyzer_mentions: list[dict], regex_mentions: list[dict]) -> list[dict]:
    if not regex_mentions:
        return analyzer_mentions
    analyzer_by_text = {
        _normalise_text(item.get("text")).casefold(): item
        for item in analyzer_mentions
    }
    merged = []
    for mention in regex_mentions:
        key = _normalise_text(mention.get("text")).casefold()
        analyzer_item = analyzer_by_text.get(key)
        if analyzer_item:
            merged.append({**mention, "source": "analyzer", "field": analyzer_item.get("field", "dates")})
        else:
            merged.append(mention)
    return merged


def _extract_date_mentions(text: str) -> list[dict]:
    mentions: list[dict] = []
    for pattern_name, pattern in (
        ("iso_date", _ISO_DATE_RE),
        ("slash_date", _SLASH_DATE_RE),
        ("month_date", _MONTH_DATE_RE),
    ):
        for match in pattern.finditer(text):
            mentions.append(
                {
                    "text": match.group(0),
                    "source": "regex",
                    "field": pattern_name,
                    "excerpt": _excerpt_around(text, match.start(), match.end()),
                }
            )
    return mentions


def _extract_time_mentions(text: str) -> list[dict]:
    mentions = []
    for match in _TIME_RE.finditer(text):
        mentions.append(
            {
                "text": match.group(0),
                "source": "regex",
                "field": "time",
                "excerpt": _excerpt_around(text, match.start(), match.end()),
            }
        )
    return mentions


def _matching_time_mentions(date_mention: dict, time_mentions: list[dict]) -> list[dict]:
    if not time_mentions:
        return []

    date_excerpt = _normalise_text(date_mention.get("excerpt"))
    if not date_excerpt:
        return []

    matches = []
    for time_mention in time_mentions:
        time_excerpt = _normalise_text(time_mention.get("excerpt"))
        time_text = _normalise_text(time_mention.get("text"))
        if not time_text:
            continue
        if time_text in date_excerpt or (time_excerpt and _excerpts_overlap(date_excerpt, time_excerpt)):
            matches.append(time_mention)
    return matches[:2]


def _excerpts_overlap(a: str, b: str) -> bool:
    a_tokens = set(re.findall(r"[A-Za-z0-9-]{4,}", a.casefold()))
    b_tokens = set(re.findall(r"[A-Za-z0-9-]{4,}", b.casefold()))
    return len(a_tokens.intersection(b_tokens)) >= 3


def _candidate_from_mentions(
    *,
    evidence_id: str,
    filename: str,
    date_mention: dict,
    time_mention: dict | None,
    statements: list[str],
    findings: list[str],
    snippet: str,
) -> dict:
    date_parse = _parse_date_token(str(date_mention.get("text", "")))
    time_parse = _parse_time_token(str(time_mention.get("text", ""))) if time_mention else {}
    sort_time = None
    ambiguous = bool(date_parse.get("ambiguous"))
    ambiguity_reason = date_parse.get("ambiguity_reason")

    if not ambiguous and date_parse.get("date"):
        sort_time = _combine_date_time(date_parse["date"], time_parse)

    # Same-context invariant: date and time must come from the same record/sentence.
    # _matching_time_mentions() already enforces this via excerpt overlap.
    # We record same_context_verified = True only when a time is matched from the
    # same excerpt as the date — never combined across different records.
    same_context_verified = time_mention is not None

    # Date-type classification: classify the role of this date in the evidence.
    date_type = _classify_date_type(
        date_mention=date_mention,
        snippet=snippet,
        statements=statements,
    )

    return {
        "evidence_id": evidence_id,
        "filename": filename,
        "date_text": str(date_mention.get("text", "")),
        "time_text": str(time_mention.get("text", "")) if time_mention else "",
        "sort_time": sort_time,
        "precision": "minute" if time_mention else "day",
        "timezone": time_parse.get("timezone"),
        "ambiguous": ambiguous,
        "ambiguity_reason": ambiguity_reason,
        "same_context_verified": same_context_verified,
        "date_type": date_type,
        "statements": statements,
        "findings": findings,
        "snippet": snippet,
        "provenance": [
            {
                "source": date_mention.get("source"),
                "field": date_mention.get("field"),
                "value": date_mention.get("text"),
                "excerpt": date_mention.get("excerpt"),
            },
            *(
                [
                    {
                        "source": time_mention.get("source"),
                        "field": time_mention.get("field"),
                        "value": time_mention.get("text"),
                        "excerpt": time_mention.get("excerpt"),
                    }
                ]
                if time_mention
                else []
            ),
        ],
    }


def _compact_candidates_for_llm(candidates: list[dict]) -> list[dict]:
    """Convert candidates to compact LLM-friendly format. Does NOT truncate."""
    compact = []
    for index, candidate in enumerate(candidates, start=1):
        compact.append(
            {
                "candidate_id": f"T-{index:03d}",
                "evidence_id": candidate["evidence_id"],
                "filename": candidate["filename"],
                "date_text": candidate["date_text"],
                "time_text": candidate["time_text"],
                "sort_time": candidate["sort_time"],
                "ambiguous": candidate["ambiguous"],
                "ambiguity_reason": candidate.get("ambiguity_reason"),
                "same_context_verified": candidate.get("same_context_verified", False),
                "date_type": candidate.get("date_type", "AMBIGUOUS"),
                "statements": candidate.get("statements", []),
                "findings": candidate.get("findings", []),
                "snippet": candidate.get("snippet", ""),
            }
        )
    return compact  # Splitting is handled by _split_candidate_batches


def _split_candidate_batches(
    compact_candidates: list[dict],
    *,
    budget: object,
) -> list[list[dict]]:
    """Split compact candidates into token-safe batches for batch synthesis.

    Handles any number of candidates — never discards because count is high.
    """
    system_tokens = estimate_tokens(_SYSTEM_PROMPT)
    envelope_tokens = _ENVELOPE_TOKENS
    available = budget.effective_input - system_tokens - envelope_tokens  # type: ignore[attr-defined]
    available = max(available, 200)

    batches: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0

    for item in compact_candidates:
        item_tokens = estimate_tokens(json.dumps(item, separators=(',', ':')))
        if current and current_tokens + item_tokens > available:
            batches.append(current)
            current, current_tokens = [], 0
        current.append(item)
        current_tokens += item_tokens

    if current:
        batches.append(current)
    return batches or [[]]


def _classify_date_type(
    *,
    date_mention: dict,
    snippet: str,
    statements: list[str],
) -> str:
    """Classify the temporal role of a date in the evidence.

    Returns one of: EVENT | DOCUMENT | REFERENCE | METADATA | AMBIGUOUS

    EVENT    — date describes something that happened (an action, transaction, meeting)
    DOCUMENT — date is a document creation/submission/filing date
    REFERENCE — date is cited as a past or future reference point
    METADATA — date is file metadata (created_at, modified_at, accessed_at)
    AMBIGUOUS — cannot determine from context signals
    """
    context = " ".join(filter(None, [snippet, *statements])).lower()
    field = str(date_mention.get("field", "")).lower()
    source = str(date_mention.get("source", "")).lower()

    _METADATA_WORDS = ("created", "modified", "accessed", "uploaded", "metadata", "file date", "recorded")
    _DOCUMENT_WORDS = ("dated", "signed", "issued", "filed", "submitted", "document date", "report date", "letter dated")
    _REFERENCE_WORDS = ("as of", "prior to", "before", "after", "since", "by", "effective", "reference date")
    _EVENT_WORDS = ("on", "occurred", "happened", "took place", "meeting", "transaction",
                    "payment", "transfer", "received", "sent", "arrested", "purchased", "sold")

    if any(w in context for w in _METADATA_WORDS):
        return "METADATA"
    if any(w in context for w in _DOCUMENT_WORDS):
        return "DOCUMENT"
    if any(w in context for w in _EVENT_WORDS):
        return "EVENT"
    if any(w in context for w in _REFERENCE_WORDS):
        return "REFERENCE"
    return "AMBIGUOUS"


def _fallback_timeline(candidates: list[dict]) -> dict:
    events = []
    for candidate in candidates:
        text_parts = candidate.get("statements") or candidate.get("findings") or []
        event_text = text_parts[0] if text_parts else _event_text_from_candidate(candidate)
        classification = "UNCONFIRMED" if candidate.get("ambiguous") else "FACT"
        confidence = "low" if candidate.get("ambiguous") else "medium"
        events.append(
            {
                "time": _readable_time(candidate),
                "sort_time": candidate.get("sort_time"),
                "event": event_text,
                "refs": [candidate["evidence_id"]],
                "classification": classification,
                "confidence": confidence,
            }
        )
    return {"events": events, "time_window": "Unknown"}


def _event_text_from_candidate(candidate: dict) -> str:
    date_text = _normalise_text(candidate.get("date_text"))
    time_text = _normalise_text(candidate.get("time_text"))
    for item in candidate.get("provenance", []):
        if not isinstance(item, dict):
            continue
        excerpt = _normalise_text(item.get("excerpt"))
        if excerpt and excerpt not in {date_text, time_text}:
            return _trim_event_text(excerpt, date_text, time_text)
    snippet = _normalise_text(candidate.get("snippet"))
    if snippet:
        return _trim_event_text(snippet, date_text, time_text)
    return f"Date/time reference {date_text} {time_text}".strip()


def _trim_event_text(text: str, date_text: str, time_text: str) -> str:
    clean = _normalise_text(text)
    anchor = date_text or time_text
    if anchor:
        index = clean.casefold().find(anchor.casefold())
        if index >= 0:
            left = max(0, index - 90)
            right = min(len(clean), index + len(anchor) + 110)
            clean = clean[left:right].strip(" ,.;:-")
    if len(clean) > 180:
        clean = clean[:177].rstrip() + "..."
    return clean or "Temporal evidence reference"


def _validate_and_sort_events(
    events: object,
    *,
    candidates: list[dict],
    valid_evidence_ids: set[str],
) -> list[dict]:
    if not isinstance(events, list):
        return []

    candidate_sort_times = {
        (candidate["evidence_id"], candidate.get("sort_time"))
        for candidate in candidates
        if candidate.get("sort_time")
    }
    candidate_ambiguous_refs = {
        candidate["evidence_id"] for candidate in candidates if candidate.get("ambiguous")
    }
    provenance_by_ref_time = _provenance_index(candidates)

    cleaned_events = []
    seen: set[tuple] = set()
    for event in events:
        if not isinstance(event, dict):
            continue

        refs = [
            ref
            for ref in _clean_list(event.get("refs", []))
            if ref in valid_evidence_ids
        ]
        if not refs:
            continue

        sort_time = event.get("sort_time")
        sort_time = str(sort_time).strip() if sort_time else None
        if sort_time and not _is_valid_iso(sort_time):
            sort_time = None

        classification = str(event.get("classification") or "UNCONFIRMED").upper()
        if classification not in _VALID_CLASSIFICATIONS:
            classification = "UNCONFIRMED"

        if any(ref in candidate_ambiguous_refs for ref in refs) and not sort_time:
            classification = "UNCONFIRMED"

        confidence = str(event.get("confidence") or "medium").lower()
        if confidence not in _VALID_CONFIDENCE:
            confidence = "medium"

        event_text = _normalise_text(event.get("event", ""))
        if not event_text:
            continue

        time_text = _normalise_text(event.get("time", "")) or "Unknown"
        if sort_time and not any((ref, sort_time) in candidate_sort_times for ref in refs):
            sort_time = None
            if classification == "FACT":
                classification = "INFERRED"

        if any(ref in candidate_ambiguous_refs for ref in refs) and not sort_time:
            classification = "UNCONFIRMED"

        identity = (tuple(sorted(refs)), sort_time, event_text.casefold())
        if identity in seen:
            continue
        seen.add(identity)

        cleaned_events.append(
            {
                "time": time_text,
                "sort_time": sort_time,
                "event": event_text,
                "refs": refs,
                "classification": classification,
                "confidence": confidence,
                "provenance": _event_provenance(
                    refs=refs,
                    sort_time=sort_time,
                    index=provenance_by_ref_time,
                ),
            }
        )

    return sorted(cleaned_events, key=_event_sort_key)


def _event_sort_key(event: dict) -> tuple[int, datetime, str]:
    sort_time = event.get("sort_time")
    parsed = _parse_iso_datetime(sort_time) if sort_time else None
    if not parsed:
        return (1, datetime.max.replace(tzinfo=timezone.utc), event.get("time", ""))
    return (0, parsed, event.get("time", ""))


def _compute_time_window(events: list[dict]) -> str:
    known = [event for event in events if event.get("sort_time")]
    if not known:
        return "Unknown"
    first = known[0]["time"]
    last = known[-1]["time"]
    return first if first == last else f"{first} - {last}"


def _build_output(events: list[dict], time_window: str, candidates: list[dict]) -> dict:
    return {
        "events": events,
        "time_window": time_window,
        "event_count": len(events),
        "candidate_count": len(candidates),
        "ambiguous_candidate_count": sum(1 for item in candidates if item.get("ambiguous")),
        "fact_count": sum(1 for e in events if e.get("classification") == "FACT"),
        "inferred_count": sum(1 for e in events if e.get("classification") == "INFERRED"),
        "unconfirmed_count": sum(1 for e in events if e.get("classification") == "UNCONFIRMED"),
        "temporal_candidates": _compact_candidates_for_llm(candidates),
    }


def _provenance_index(candidates: list[dict]) -> dict[tuple[str, str | None], list[dict]]:
    index: dict[tuple[str, str | None], list[dict]] = {}
    for candidate in candidates:
        key = (candidate["evidence_id"], candidate.get("sort_time"))
        index.setdefault(key, []).append(
            {
                "evidence_id": candidate["evidence_id"],
                "filename": candidate.get("filename", "unknown"),
                "date_text": candidate.get("date_text", ""),
                "time_text": candidate.get("time_text", ""),
                "sort_time": candidate.get("sort_time"),
                "ambiguous": candidate.get("ambiguous", False),
                "ambiguity_reason": candidate.get("ambiguity_reason"),
                "provenance": candidate.get("provenance", []),
            }
        )
    return index


def _event_provenance(
    *,
    refs: list[str],
    sort_time: str | None,
    index: dict[tuple[str, str | None], list[dict]],
) -> list[dict]:
    provenance = []
    seen: set[tuple[str, str | None, str, str]] = set()
    for ref in refs:
        matches = index.get((ref, sort_time), [])
        if not matches and sort_time is not None:
            matches = index.get((ref, None), [])
        for item in matches:
            identity = (
                item["evidence_id"],
                item.get("sort_time"),
                item.get("date_text", ""),
                item.get("time_text", ""),
            )
            if identity in seen:
                continue
            seen.add(identity)
            provenance.append(item)
    return provenance


def _parse_date_token(value: str) -> dict:
    text = value.strip()
    if not text:
        return {"date": None, "ambiguous": True, "ambiguity_reason": "empty_date"}

    match = _ISO_DATE_RE.search(text)
    if match:
        return _date_result(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )

    match = _SLASH_DATE_RE.search(text)
    if match:
        a = int(match.group("a"))
        b = int(match.group("b"))
        year = int(match.group("year"))
        if a <= 12 and b <= 12:
            return {
                "date": None,
                "ambiguous": True,
                "ambiguity_reason": "slash_date_could_be_dd_mm_or_mm_dd",
            }
        if a > 12:
            return _date_result(year, b, a)
        return _date_result(year, a, b)

    match = _MONTH_DATE_RE.search(text)
    if match:
        month_text = match.group("month_name") or match.group("month_name_first")
        day_text = match.group("day") or match.group("day_after")
        return _date_result(
            int(match.group("year")),
            _MONTHS[month_text.casefold()],
            int(day_text),
        )

    return {"date": None, "ambiguous": True, "ambiguity_reason": "unsupported_date_format"}


def _date_result(year: int, month: int, day: int) -> dict:
    try:
        return {
            "date": datetime(year, month, day),
            "ambiguous": False,
            "ambiguity_reason": None,
        }
    except ValueError:
        return {"date": None, "ambiguous": True, "ambiguity_reason": "invalid_calendar_date"}


def _parse_time_token(value: str) -> dict:
    text = value.strip()
    match = _TIME_RE.search(text)
    if not match:
        return {}

    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second") or 0)
    ampm = match.group("ampm")
    if ampm:
        marker = ampm.lower()
        if marker == "pm" and hour < 12:
            hour += 12
        elif marker == "am" and hour == 12:
            hour = 0

    timezone_text = match.group("tz")
    return {
        "hour": hour,
        "minute": minute,
        "second": second,
        "timezone": _normalise_timezone(timezone_text),
    }


def _combine_date_time(date_value: datetime, time_parse: dict) -> str:
    hour = int(time_parse.get("hour", 0))
    minute = int(time_parse.get("minute", 0))
    second = int(time_parse.get("second", 0))
    dt = date_value.replace(hour=hour, minute=minute, second=second)
    tz_text = time_parse.get("timezone")
    tzinfo = _timezone_info(tz_text)
    if tzinfo:
        dt = dt.replace(tzinfo=tzinfo)
    return dt.isoformat()


def _normalise_timezone(value: str | None) -> str | None:
    if not value:
        return None
    if value == "Z":
        return "Z"
    if re.fullmatch(r"[+-]\d{2}\d{2}", value):
        return f"{value[:3]}:{value[3:]}"
    return value


def _timezone_info(value: str | None) -> timezone | None:
    if not value:
        return None
    if value == "Z":
        return timezone.utc
    match = re.fullmatch(r"(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2})", value)
    if not match:
        return None
    delta = timedelta(
        hours=int(match.group("hours")),
        minutes=int(match.group("minutes")),
    )
    if match.group("sign") == "-":
        delta = -delta
    return timezone(delta)


def _is_valid_iso(value: str) -> bool:
    return _parse_iso_datetime(value) is not None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _readable_time(candidate: dict) -> str:
    date_text = candidate.get("date_text", "")
    time_text = candidate.get("time_text", "")
    return f"{date_text} {time_text}".strip() or "Unknown"


def _dedupe_mentions(mentions: list[dict]) -> list[dict]:
    unique = []
    seen: set[tuple[str, str]] = set()
    for mention in mentions:
        key = (
            str(mention.get("text", "")).casefold(),
            str(mention.get("field", "")).casefold(),
        )
        if not key[0] or key in seen:
            continue
        seen.add(key)
        unique.append(mention)
    return unique


def _clean_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = []
    seen = set()
    for value in values:
        text = _normalise_text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _normalise_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _snippet(text: str, limit: int = _MAX_TEXT_SNIPPET_CHARS) -> str:
    return text[:limit].strip()


def _excerpt_around(text: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return _normalise_text(text[left:right])


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
