"""
agents/report_generator.py
--------------------------
Agent 06 - Report Generator

Synthesises the validated outputs from agents 01-05 into one evidence-grounded
case report. The saved document keeps the existing frontend contract while also
including richer validated metadata for audit and later exports.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Awaitable, Callable, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.database import collections
from app.services.investigation_service import update_investigation_status
from app.services.llm_service import (
    ContextTooLargeError,
    chat_completion_json,
    emit_llm_failure_telemetry,
)
from app.services.report_service import save_report
from app.utils.context_budget import (
    compact_json_size,
    estimate_tokens,
    get_token_budget,
    truncate_by_semantic_priority,
    validate_payload,
)
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

REPORT_VERSION = "1.0"
_MAX_CONTEXT_CHARS = 18000
_MAX_FINDINGS = 12
_MAX_TIMELINE = 20
_MAX_CONNECTIONS = 16
_MAX_PERSONS = 12
_MAX_UNRESOLVED = 12
_EVIDENCE_ID_RE = re.compile(r"\bE-\d{3,}\b", re.IGNORECASE)
_BRACKETED_EVIDENCE_RE = re.compile(r"\[(E-\d{3,})\]", re.IGNORECASE)
_ACCUSATORY_RE = re.compile(
    r"\b(guilty|culprit|criminal|offender|perpetrator|committed|responsible for|"
    r"stole|murdered|killed|fraudster|suspect is|suspect was)\b",
    re.IGNORECASE,
)


# ── Pydantic models ────────────────────────────────────────────────────────────

class ReportFinding(BaseModel):
    text: str
    evidence_ids: list[str] = []
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    classification: Literal["FACT", "CORROBORATED", "INFERRED", "UNCONFIRMED"] = "FACT"

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _normalise_text(value)


class ReportTimelineEntry(BaseModel):
    time: str
    event: str
    refs: list[str] = []
    classification: Literal["FACT", "INFERRED", "UNCONFIRMED"] = "FACT"
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5

    @field_validator("time", "event")
    @classmethod
    def clean_text_fields(cls, value: str) -> str:
        return _normalise_text(value)


class ReportConnection(BaseModel):
    a: str
    b: str
    reason: str
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5

    @field_validator("a", "b")
    @classmethod
    def clean_ref(cls, value: str) -> str:
        return _normalise_evidence_id(value)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        return _normalise_text(value)


class ReportPerson(BaseModel):
    name: str
    note: str
    evidence_ids: list[str] = []
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    involvement_indicator: Literal["High", "Moderate", "Low", "Unknown"] = "Unknown"

    @field_validator("name", "note")
    @classmethod
    def clean_text_fields(cls, value: str) -> str:
        return _normalise_text(value)


class InternalReport(BaseModel):
    executive_summary: str
    findings: list[ReportFinding] = []
    timeline: list[ReportTimelineEntry] = []
    connections: list[ReportConnection] = []
    persons: list[ReportPerson] = []
    unresolved: list[str] = []

    @field_validator("executive_summary")
    @classmethod
    def clean_summary(cls, value: str) -> str:
        return _normalise_text(value)

    @field_validator("unresolved")
    @classmethod
    def clean_unresolved(cls, values: list[str]) -> list[str]:
        return [_normalise_text(value) for value in values if _normalise_text(value)]

    @model_validator(mode="after")
    def require_report_content(self) -> "InternalReport":
        if not (
            self.executive_summary
            or self.findings
            or self.timeline
            or self.connections
            or self.persons
            or self.unresolved
        ):
            raise ValueError("report contains no usable content")
        return self


# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a senior forensic investigator writing a final case report.
Use only the provided outputs from the previous Verito agents.

Return ONLY valid JSON in this internal schema:
{
  "executive_summary": "2-4 neutral sentences",
  "findings": [
    {
      "text": "Evidence-grounded finding",
      "evidence_ids": ["E-001"],
      "confidence": 0.85,
      "classification": "FACT"
    }
  ],
  "timeline": [
    {
      "time": "readable date/time",
      "event": "brief event",
      "refs": ["E-001"],
      "classification": "FACT",
      "confidence": 0.8
    }
  ],
  "connections": [
    {
      "a": "E-001",
      "b": "E-002",
      "reason": "Supported relationship between evidence items",
      "confidence": 0.75
    }
  ],
  "persons": [
    {
      "name": "Name",
      "note": "Neutral description of how the person appears in the evidence",
      "evidence_ids": ["E-001"],
      "confidence": 0.7,
      "involvement_indicator": "Low"
    }
  ],
  "unresolved": ["Gap, contradiction, or uncertainty"]
}

Rules:
- Do not invent facts, dates, people, organisations, locations, relationships, or evidence IDs.
- Every finding must include at least one valid evidence ID.
- Preserve FACT, CORROBORATED, INFERRED, and UNCONFIRMED distinctions.
- Use neutral language. Do not state or imply guilt, culprit, criminality, or responsibility for wrongdoing.
- A person appearing in evidence is not proof of wrongdoing.
- Prefer phrases like "appears in the evidence", "is associated with", "is mentioned in", and "may warrant further review"."""


# ── Main entry point ───────────────────────────────────────────────────────────

async def run(
    investigation_id: str,
    emit: Emitter,
    collector_output: dict,
    analyzer_output: dict,
    timeline_output: dict,
    linker_output: dict,
    subject_output: dict,
    investigation_prompt: str,
) -> dict:
    """Run the Report Generator agent."""
    agent_id = "report"
    await _set_status(investigation_id, agent_id, "running")
    lines: list[dict] = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    await add_line("info", "Collecting previous agent outputs...")
    valid_evidence_ids = _valid_evidence_ids(collector_output)

    await add_line("info", "Building synthesis context...")
    synthesis_context = _build_synthesis_context(
        investigation_prompt=investigation_prompt,
        collector_output=collector_output,
        analyzer_output=analyzer_output,
        timeline_output=timeline_output,
        linker_output=linker_output,
        subject_output=subject_output,
        valid_evidence_ids=valid_evidence_ids,
    )

    await add_line("info", "Preparing fallback report...")
    fallback_report = _build_fallback_report(
        collector_output=collector_output,
        analyzer_output=analyzer_output,
        timeline_output=timeline_output,
        linker_output=linker_output,
        subject_output=subject_output,
        valid_evidence_ids=valid_evidence_ids,
        investigation_prompt=investigation_prompt,
    )
    degraded_reasons = _degraded_reasons(
        collector_output=collector_output,
        analyzer_output=analyzer_output,
        timeline_output=timeline_output,
        linker_output=linker_output,
        subject_output=subject_output,
    )
    if degraded_reasons:
        fallback_report = _mark_report_degraded(fallback_report, degraded_reasons)

    # Token-aware payload preparation: validate before sending, reduce if needed
    budget = get_token_budget(output_reserve=3000)
    user_content = synthesis_context
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    validation = validate_payload(messages, max_output_tokens=3000, budget=budget)
    if not validation.fits:
        await add_line(
            "info",
            f"Context too large (~{validation.estimated_input_tokens} tokens); reducing before synthesis.",
        )
        raw_context = _parse_synthesis_context_dict(
            investigation_prompt=investigation_prompt,
            collector_output=collector_output,
            analyzer_output=analyzer_output,
            timeline_output=timeline_output,
            linker_output=linker_output,
            subject_output=subject_output,
            valid_evidence_ids=valid_evidence_ids,
        )
        reduced = truncate_by_semantic_priority(raw_context, budget_tokens=budget.effective_input)
        user_content = str(reduced)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    estimated_input = estimate_tokens(user_content)
    max_out = 3000
    generation_status = "degraded" if degraded_reasons else "llm_generated"
    raw_report: dict

    try:
        await add_line("scan", "Generating structured report...")
        raw_report = await chat_completion_json(messages, temperature=0.15, max_tokens=max_out)

    except ContextTooLargeError as exc:
        emit_llm_failure_telemetry(
            agent_name="report_generator",
            error_code=413, error_category="context_too_large",
            estimated_input_tokens=estimated_input,
            requested_output_tokens=max_out,
            payload_size_chars=len(user_content),
            attempt_number=1,
            was_retried=True, retry_action="section_synthesis",
            fallback_action=None,
            original_message=exc.original_message,
        )
        await add_line("scan", "Switching to section-level synthesis...")
        try:
            raw_report = await _section_level_synthesis(messages=messages, max_out=max_out, budget=budget)
        except Exception as exc2:
            emit_llm_failure_telemetry(
                agent_name="report_generator",
                error_code="unknown", error_category="unknown",
                estimated_input_tokens=estimated_input,
                requested_output_tokens=max_out, payload_size_chars=len(user_content),
                attempt_number=2,
                was_retried=False, retry_action=None,
                fallback_action="deterministic_fallback",
                original_message=str(exc2),
            )
            generation_status = "degraded_fallback_llm_error" if degraded_reasons else "fallback_llm_error"
            raw_report = fallback_report
            await add_line("warn", f"Section synthesis also failed; using deterministic fallback: {str(exc2)[:80]}")

    except ValueError as exc:
        emit_llm_failure_telemetry(
            agent_name="report_generator",
            error_code=400, error_category="json_validation",
            estimated_input_tokens=estimated_input,
            requested_output_tokens=max_out, payload_size_chars=len(user_content),
            attempt_number=1,
            was_retried=True, retry_action="section_synthesis",
            fallback_action=None,
            original_message=str(exc),
        )
        await add_line("scan", "JSON validation error; switching to section-level synthesis...")
        try:
            raw_report = await _section_level_synthesis(messages=messages, max_out=max_out, budget=budget)
        except Exception as exc2:
            emit_llm_failure_telemetry(
                agent_name="report_generator",
                error_code="unknown", error_category="unknown",
                estimated_input_tokens=estimated_input,
                requested_output_tokens=max_out, payload_size_chars=len(user_content),
                attempt_number=2,
                was_retried=False, retry_action=None,
                fallback_action="deterministic_fallback",
                original_message=str(exc2),
            )
            generation_status = "degraded_fallback_llm_error" if degraded_reasons else "fallback_llm_error"
            raw_report = fallback_report
            await add_line("warn", f"Section synthesis also failed; using deterministic fallback: {str(exc2)[:80]}")

    except Exception as exc:
        emit_llm_failure_telemetry(
            agent_name="report_generator",
            error_code="unknown", error_category="unknown",
            estimated_input_tokens=estimated_input,
            requested_output_tokens=max_out, payload_size_chars=len(user_content),
            attempt_number=1,
            was_retried=False, retry_action=None,
            fallback_action="deterministic_fallback",
            original_message=str(exc),
        )
        generation_status = "degraded_fallback_llm_error" if degraded_reasons else "fallback_llm_error"
        raw_report = fallback_report
        await add_line("warn", f"Structured generation failed; using deterministic fallback: {str(exc)[:80]}")

    if degraded_reasons:
        raw_report = _mark_report_degraded(raw_report, degraded_reasons)

    await add_line("info", "Normalizing report...")
    try:
        internal_report = _normalise_report(raw_report, valid_evidence_ids)
    except ValidationError:
        generation_status = "fallback_validation_error"
        internal_report = _normalise_report(fallback_report, valid_evidence_ids)
        await add_line("warn", "LLM report failed schema validation; fallback report prepared.")

    await add_line("info", "Validating report grounding...")
    internal_report = _validate_grounding(internal_report, fallback_report, valid_evidence_ids)

    frontend_report = _to_frontend_report(internal_report)
    stored_report = {
        **frontend_report,
        "report_version": REPORT_VERSION,
        "generated_at": utcnow(),
        "generation_status": generation_status,
        "degraded_reasons": degraded_reasons,
        "validated_report": internal_report.model_dump(),
    }

    await add_line("info", "Saving report...")
    await save_report(investigation_id, stored_report)
    final_investigation_status = "failed" if _has_severe_degradation(degraded_reasons) else "complete"
    await update_investigation_status(investigation_id, final_investigation_status)

    if degraded_reasons:
        await add_line("warn", "Report generated in degraded mode due to upstream analysis issues.")
    await add_line("ok", "Report generation completed.")
    summary = [
        f"{len(frontend_report['findings'])} finding(s)",
        f"{len(frontend_report['timeline'])} timeline event(s)",
        f"{len(frontend_report['connections'])} connection(s)",
    ]
    status = "completed_with_warnings" if degraded_reasons else "done"
    await _complete(investigation_id, agent_id, lines, summary, stored_report, status=status)
    await emit(
        "agent_completed_with_warnings" if status == "completed_with_warnings" else "agent_completed",
        {"agent_id": agent_id},
    )
    return stored_report


# ── Context builders ───────────────────────────────────────────────────────────

def _build_synthesis_context(
    *,
    investigation_prompt: str,
    collector_output: dict,
    analyzer_output: dict,
    timeline_output: dict,
    linker_output: dict,
    subject_output: dict,
    valid_evidence_ids: set[str],
) -> str:
    """Build the full synthesis context string. Shrinks via priority loop if it exceeds char limit."""
    context = {
        "investigation_query": investigation_prompt,
        "valid_evidence_ids": sorted(valid_evidence_ids),
        "collector": {
            "items_indexed": collector_output.get("items_indexed", 0),
            "items_failed": collector_output.get("items_failed", 0),
            "failed_ids": collector_output.get("failed_ids", []),
        },
        "analyzer": {
            "people": _limit(analyzer_output.get("people", []), 20),
            "organisations": _limit(analyzer_output.get("organisations", []), 20),
            "locations": _limit(analyzer_output.get("locations", []), 20),
            "dates": _limit(analyzer_output.get("dates", []), 20),
            "times": _limit(analyzer_output.get("times", []), 20),
            "contact_info": _limit(analyzer_output.get("contact_info", []), 20),
            "identifiers": _limit(analyzer_output.get("identifiers", []), 30),
            "amounts": _limit(analyzer_output.get("amounts", []), 20),
            "observations": _limit(analyzer_output.get("observations", []), 40),
            "findings": _limit(analyzer_output.get("findings", []), 30),
            "referenced_key_findings": _limit(analyzer_output.get("referenced_key_findings", []), 40),
            "referenced_key_statements": _limit(analyzer_output.get("referenced_key_statements", []), 40),
        },
        "timeline": {
            "time_window": timeline_output.get("time_window", "Unknown"),
            "events": _limit(timeline_output.get("events", []), _MAX_TIMELINE),
            "fact_count": timeline_output.get("fact_count", 0),
            "inferred_count": timeline_output.get("inferred_count", 0),
            "unconfirmed_count": timeline_output.get("unconfirmed_count", 0),
            "ambiguous_candidate_count": timeline_output.get("ambiguous_candidate_count", 0),
        },
        "linker": {
            "links": _limit(linker_output.get("links", []), _MAX_CONNECTIONS),
            "strong_links": linker_output.get("strong_links", 0),
            "moderate_links": linker_output.get("moderate_links", 0),
            "weak_links": linker_output.get("weak_links", 0),
            "link_types": linker_output.get("link_types", {}),
        },
        "subject": {
            "persons": _limit(subject_output.get("persons", []), _MAX_PERSONS),
            "subject_count": subject_output.get("subject_count", 0),
            "high_involvement_count": subject_output.get("high_involvement_count", 0),
            "moderate_involvement_count": subject_output.get("moderate_involvement_count", 0),
            "low_involvement_count": subject_output.get("low_involvement_count", 0),
        },
    }
    # Priority-based character reduction (secondary to token validation above)
    while compact_json_size(context) > _MAX_CONTEXT_CHARS:
        analyzer = context["analyzer"]
        timeline = context["timeline"]
        linker = context["linker"]
        subject = context["subject"]
        if len(analyzer["referenced_key_statements"]) > 12:
            analyzer["referenced_key_statements"] = analyzer["referenced_key_statements"][:max(12, len(analyzer["referenced_key_statements"]) // 2)]
        elif len(analyzer["referenced_key_findings"]) > 12:
            analyzer["referenced_key_findings"] = analyzer["referenced_key_findings"][:max(12, len(analyzer["referenced_key_findings"]) // 2)]
        elif len(timeline["events"]) > 8:
            timeline["events"] = timeline["events"][:max(8, len(timeline["events"]) // 2)]
        elif len(linker["links"]) > 6:
            linker["links"] = linker["links"][:max(6, len(linker["links"]) // 2)]
        elif len(subject["persons"]) > 6:
            subject["persons"] = subject["persons"][:max(6, len(subject["persons"]) // 2)]
        else:
            break
    return json.dumps(context, ensure_ascii=True, separators=(",", ":"))


def _parse_synthesis_context_dict(
    *,
    investigation_prompt: str,
    collector_output: dict,
    analyzer_output: dict,
    timeline_output: dict,
    linker_output: dict,
    subject_output: dict,
    valid_evidence_ids: set[str],
) -> dict:
    """Return a compact context dict for semantic priority reduction.

    Called only when validate_payload() confirms the context won't fit.
    Result is passed to truncate_by_semantic_priority() before re-serialising.
    """
    return {
        "investigation_query": investigation_prompt,
        "valid_evidence_ids": sorted(valid_evidence_ids),
        "collector": {"items_indexed": collector_output.get("items_indexed", 0)},
        "analyzer": {
            "people": _limit(analyzer_output.get("people", []), 20),
            "organisations": _limit(analyzer_output.get("organisations", []), 15),
            "identifiers": _limit(analyzer_output.get("identifiers", []), 20),
            "amounts": _limit(analyzer_output.get("amounts", []), 15),
            "findings": _limit(analyzer_output.get("findings", []), 20),
            "referenced_key_findings": _limit(analyzer_output.get("referenced_key_findings", []), 25),
            "referenced_key_statements": _limit(analyzer_output.get("referenced_key_statements", []), 20),
        },
        "timeline": {
            "time_window": timeline_output.get("time_window", "Unknown"),
            "events": _limit(timeline_output.get("events", []), _MAX_TIMELINE),
        },
        "linker": {"links": _limit(linker_output.get("links", []), _MAX_CONNECTIONS)},
        "subject": {"persons": _limit(subject_output.get("persons", []), _MAX_PERSONS)},
    }


async def _section_level_synthesis(
    *,
    messages: list[dict],
    max_out: int,
    budget: object,
) -> dict:
    """Generate report sections one at a time when full synthesis fails (413/400).

    Splits into two section groups and merges results.
    Only called as a retry after a 413 or 400 error.
    """
    user_content = messages[-1]["content"] if messages else ""
    section_out = max(800, max_out // 4)
    ctx = user_content[:8000]

    try:
        part1 = await chat_completion_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Generate ONLY executive_summary and findings.\nContext:\n{ctx}"},
            ],
            temperature=0.15,
            max_tokens=section_out,
        )
    except Exception:
        part1 = {}

    try:
        part2 = await chat_completion_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Generate ONLY timeline, connections, persons, and unresolved.\nContext:\n{ctx}"},
            ],
            temperature=0.15,
            max_tokens=section_out,
        )
    except Exception:
        part2 = {}

    return {
        "executive_summary": part1.get("executive_summary") or part2.get("executive_summary") or "",
        "findings": part1.get("findings") or [],
        "timeline": part2.get("timeline") or [],
        "connections": part2.get("connections") or [],
        "persons": part2.get("persons") or [],
        "unresolved": part2.get("unresolved") or [],
    }


# ── Report normalisation and grounding ────────────────────────────────────────

def _normalise_report(report: object, valid_evidence_ids: set[str]) -> InternalReport:
    if not isinstance(report, dict):
        report = {}
    data = {
        "executive_summary": _neutralise_text(
            report.get("executive_summary") or report.get("summary") or ""
        ),
        "findings": _normalise_findings(report.get("findings", []), valid_evidence_ids),
        "timeline": _normalise_timeline(report.get("timeline", []), valid_evidence_ids),
        "connections": _normalise_connections(report.get("connections", []), valid_evidence_ids),
        "persons": _normalise_persons(report.get("persons", []), valid_evidence_ids),
        "unresolved": _normalise_unresolved(report.get("unresolved", [])),
    }
    return InternalReport.model_validate(data)


def _validate_grounding(
    report: InternalReport,
    fallback_report: dict,
    valid_evidence_ids: set[str],
) -> InternalReport:
    clean_findings = [
        finding
        for finding in report.findings
        if finding.evidence_ids and not _contains_accusatory_claim(finding.text)
    ]
    clean_persons = [
        person.model_copy(update={"note": _safe_person_note(person.note)})
        for person in report.persons
        if person.name and person.evidence_ids
    ]
    clean_timeline = [
        event
        for event in report.timeline
        if event.refs and not _contains_accusatory_claim(event.event)
    ]
    clean_connections = [
        connection
        for connection in report.connections
        if connection.a in valid_evidence_ids and connection.b in valid_evidence_ids
    ]
    updated = report.model_copy(
        update={
            "executive_summary": _neutralise_text(report.executive_summary),
            "findings": clean_findings[:_MAX_FINDINGS],
            "timeline": clean_timeline[:_MAX_TIMELINE],
            "connections": clean_connections[:_MAX_CONNECTIONS],
            "persons": clean_persons[:_MAX_PERSONS],
        }
    )
    if updated.findings:
        return updated
    return _normalise_report(fallback_report, valid_evidence_ids)


def _to_frontend_report(report: InternalReport) -> dict:
    return {
        "summary": report.executive_summary,
        "findings": [
            _append_refs(
                _classification_prefix(finding.classification, finding.text),
                finding.evidence_ids,
            )
            for finding in report.findings
        ],
        "timeline": [
            {
                "time": event.time,
                "event": _classification_prefix(event.classification, event.event),
                "refs": event.refs,
            }
            for event in report.timeline
        ],
        "connections": [
            {"a": connection.a, "b": connection.b, "reason": connection.reason}
            for connection in report.connections
        ],
        "persons": [
            {
                "name": person.name,
                "note": _append_refs(_safe_person_note(person.note), person.evidence_ids),
            }
            for person in report.persons
        ],
        "unresolved": report.unresolved,
    }


# ── Fallback report builders ───────────────────────────────────────────────────

def _build_fallback_report(
    *,
    collector_output: dict,
    analyzer_output: dict,
    timeline_output: dict,
    linker_output: dict,
    subject_output: dict,
    valid_evidence_ids: set[str],
    investigation_prompt: str,
) -> dict:
    findings = _ranked_fallback_findings(analyzer_output, valid_evidence_ids)
    if not findings:
        findings = [
            {
                "text": text,
                "evidence_ids": _refs_from_text(text, valid_evidence_ids) or sorted(valid_evidence_ids)[:1],
                "confidence": 0.55,
                "classification": "UNCONFIRMED",
            }
            for text in analyzer_output.get("key_findings", [])
            if _normalise_text(text)
        ]

    timeline = [
        {
            "time": event.get("time", "Unknown"),
            "event": event.get("event", ""),
            "refs": _clean_refs(event.get("refs", []), valid_evidence_ids),
            "classification": event.get("classification", "FACT"),
            "confidence": _confidence_to_float(event.get("confidence", 0.65)),
        }
        for event in timeline_output.get("events", [])
        if isinstance(event, dict)
    ]
    connections = [
        {
            "a": link.get("a", ""),
            "b": link.get("b", ""),
            "reason": link.get("reason", ""),
            "confidence": _confidence_to_float(link.get("confidence", 0.65)),
        }
        for link in linker_output.get("links", [])
        if isinstance(link, dict)
    ]
    persons = [
        {
            "name": person.get("name", "Unknown"),
            "note": person.get("note", ""),
            "evidence_ids": _clean_refs(person.get("evidence_refs", []), valid_evidence_ids),
            "confidence": _confidence_to_float(person.get("confidence", 0.6)),
            "involvement_indicator": person.get("involvement_indicator", "Unknown"),
        }
        for person in subject_output.get("persons", [])
        if isinstance(person, dict)
    ]

    unresolved: list[str] = []
    if collector_output.get("items_failed", 0):
        unresolved.append(f"{collector_output.get('items_failed')} evidence item(s) could not be indexed.")
    if analyzer_output.get("chunks_failed", 0):
        unresolved.append(f"Evidence Analyzer failed on {analyzer_output.get('chunks_failed')} chunk(s).")
    if analyzer_output.get("llm_fallback_chunks", 0):
        unresolved.append(f"Evidence Analyzer used deterministic fallback on {analyzer_output.get('llm_fallback_chunks')} chunk(s).")
    for name, output in (
        ("Timeline Builder", timeline_output),
        ("Evidence Linker", linker_output),
        ("Subject Analyzer", subject_output),
    ):
        if isinstance(output, dict) and output.get("blocked"):
            unresolved.append(f"{name} was blocked: {output.get('reason')}")
    if timeline_output.get("ambiguous_candidate_count", 0):
        unresolved.append(f"{timeline_output.get('ambiguous_candidate_count')} temporal reference(s) remain ambiguous.")
    if linker_output.get("weak_links", 0):
        unresolved.append(f"{linker_output.get('weak_links')} weak evidence link(s) may need manual review.")
    if not valid_evidence_ids:
        unresolved.append("No validated evidence IDs were available for grounding.")
    if not unresolved:
        unresolved.append("No additional unresolved issues were identified by the prior agents.")

    summary = (
        f"Report generated for investigation query: {_normalise_text(investigation_prompt) or 'N/A'}. "
        f"The prior agents indexed {collector_output.get('items_indexed', len(valid_evidence_ids))} "
        "evidence item(s) and produced the findings, timeline, links, and subject notes below. "
        "All statements should be treated as evidence-grounded observations, not determinations of guilt."
    )
    return {
        "executive_summary": summary,
        "findings": findings[:_MAX_FINDINGS],
        "timeline": timeline[:_MAX_TIMELINE],
        "connections": connections[:_MAX_CONNECTIONS],
        "persons": persons[:_MAX_PERSONS],
        "unresolved": unresolved[:_MAX_UNRESOLVED],
    }


def _ranked_fallback_findings(analyzer_output: dict, valid_evidence_ids: set[str]) -> list[dict]:
    candidates = []
    canonical_findings = analyzer_output.get("findings", [])
    provenance_index = analyzer_output.get("provenance_index", {})

    if isinstance(canonical_findings, list):
        for item in canonical_findings:
            if not isinstance(item, dict):
                continue
            refs = []
            for provenance_id in item.get("observation_refs", []):
                provenance = provenance_index.get(provenance_id, {})
                refs.extend(_clean_refs([provenance.get("evidence_id")], valid_evidence_ids))
            text = _normalise_text(item.get("text"))
            if text and refs:
                candidates.append({
                    "text": text,
                    "evidence_ids": _dedupe_list(refs),
                    "confidence": _confidence_to_float(item.get("confidence", 0.7)),
                    "classification": "FACT",
                    "_score": 3.0 + len(set(refs)),
                })

    referenced_findings = analyzer_output.get("referenced_key_findings", [])
    if isinstance(referenced_findings, list):
        for item in referenced_findings:
            if not isinstance(item, dict):
                continue
            text = _normalise_text(item.get("text"))
            refs = _clean_refs([item.get("evidence_id")], valid_evidence_ids)
            if text and refs:
                candidates.append({
                    "text": text,
                    "evidence_ids": refs,
                    "confidence": 0.75,
                    "classification": "FACT",
                    "_score": 2.0 + len(set(refs)),
                })

    unique = _dedupe_dicts(candidates, ("text", "evidence_ids"))
    ranked = sorted(unique, key=lambda item: (-float(item.get("_score", 0)), item["text"].casefold()))
    for item in ranked:
        item.pop("_score", None)
    return ranked[:_MAX_FINDINGS]


def _degraded_reasons(
    *,
    collector_output: dict,
    analyzer_output: dict,
    timeline_output: dict,
    linker_output: dict,
    subject_output: dict,
) -> list[str]:
    reasons = []
    if collector_output.get("items_failed", 0):
        reasons.append(f"{collector_output.get('items_failed')} evidence item(s) failed during collection.")
    if analyzer_output.get("chunks_failed", 0):
        reasons.append(f"Analyzer failed on {analyzer_output.get('chunks_failed')} chunk(s).")
    if analyzer_output.get("chunks_analyzed", 0) == 0 and analyzer_output.get("chunks_failed", 0):
        reasons.append("Analyzer produced no usable chunk analyses.")
    fallback_chunks = int(analyzer_output.get("llm_fallback_chunks", 0) or 0)
    analysed_chunks = int(analyzer_output.get("chunks_analyzed", 0) or 0)
    if fallback_chunks and (analysed_chunks == 0 or fallback_chunks / max(analysed_chunks, 1) >= 0.25):
        reasons.append(f"Analyzer used deterministic fallback for {fallback_chunks} chunk(s).")
    for name, output in (
        ("Timeline Builder", timeline_output),
        ("Evidence Linker", linker_output),
        ("Subject Analyzer", subject_output),
    ):
        if isinstance(output, dict) and output.get("blocked"):
            reasons.append(f"{name} blocked: {output.get('reason')}")
    return _dedupe_list([reason for reason in reasons if reason])


def _mark_report_degraded(report: dict, reasons: list[str]) -> dict:
    updated = dict(report)
    reason_text = " ".join(reasons)
    updated["executive_summary"] = (
        "DEGRADED REPORT: Upstream analysis did not fully complete, so this report "
        f"contains only evidence-grounded partial results. {reason_text}"
    )
    unresolved = list(updated.get("unresolved", []))
    unresolved.extend(reasons)
    updated["unresolved"] = _dedupe_list(unresolved)[:_MAX_UNRESOLVED]
    return updated


def _has_severe_degradation(reasons: list[str]) -> bool:
    severe_markers = (
        "Analyzer produced no usable chunk analyses",
        "Timeline Builder blocked",
        "Evidence Linker blocked",
        "Subject Analyzer blocked",
    )
    return any(any(marker in reason for marker in severe_markers) for reason in reasons)


# ── Item normalisation helpers ─────────────────────────────────────────────────

def _normalise_findings(items: object, valid_evidence_ids: set[str]) -> list[dict]:
    findings = []
    if not isinstance(items, list):
        return findings
    for item in items:
        if isinstance(item, str):
            text = item
            refs = _refs_from_text(item, valid_evidence_ids)
            confidence = 0.55
            classification = "UNCONFIRMED"
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("finding") or "")
            refs = _clean_refs(
                item.get("evidence_ids") or item.get("refs") or _refs_from_text(text, valid_evidence_ids),
                valid_evidence_ids,
            )
            confidence = _confidence_to_float(item.get("confidence", 0.6))
            classification = _valid_choice(
                item.get("classification"), {"FACT", "CORROBORATED", "INFERRED", "UNCONFIRMED"}, "FACT"
            )
        else:
            continue
        text = _neutralise_text(text)
        if text and refs:
            findings.append({
                "text": text,
                "evidence_ids": refs,
                "confidence": confidence,
                "classification": classification,
            })
    return _dedupe_dicts(findings, ("text", "evidence_ids"))[:_MAX_FINDINGS]


def _normalise_timeline(items: object, valid_evidence_ids: set[str]) -> list[dict]:
    if not isinstance(items, list):
        return []
    events = []
    for item in items:
        if not isinstance(item, dict):
            continue
        refs = _clean_refs(item.get("refs") or item.get("evidence_ids") or [], valid_evidence_ids)
        event = _neutralise_text(item.get("event", ""))
        if not refs or not event:
            continue
        events.append({
            "time": _normalise_text(item.get("time", "Unknown")) or "Unknown",
            "event": event,
            "refs": refs,
            "classification": _valid_choice(item.get("classification"), {"FACT", "INFERRED", "UNCONFIRMED"}, "FACT"),
            "confidence": _confidence_to_float(item.get("confidence", 0.65)),
        })
    return _dedupe_dicts(events, ("time", "event", "refs"))[:_MAX_TIMELINE]


def _normalise_connections(items: object, valid_evidence_ids: set[str]) -> list[dict]:
    if not isinstance(items, list):
        return []
    connections = []
    for item in items:
        if not isinstance(item, dict):
            continue
        a = _normalise_evidence_id(item.get("a", ""))
        b = _normalise_evidence_id(item.get("b", ""))
        reason = _neutralise_text(item.get("reason", ""))
        if not a or not b or a == b or a not in valid_evidence_ids or b not in valid_evidence_ids:
            continue
        if not reason:
            continue
        connections.append({
            "a": a,
            "b": b,
            "reason": reason,
            "confidence": _confidence_to_float(item.get("confidence", 0.65)),
        })
    return _dedupe_dicts(connections, ("a", "b", "reason"))[:_MAX_CONNECTIONS]


def _normalise_persons(items: object, valid_evidence_ids: set[str]) -> list[dict]:
    if not isinstance(items, list):
        return []
    persons = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _normalise_text(item.get("name", ""))
        refs = _clean_refs(item.get("evidence_ids") or item.get("evidence_refs") or [], valid_evidence_ids)
        note = _safe_person_note(item.get("note", ""))
        if not name or not refs:
            continue
        persons.append({
            "name": name,
            "note": note,
            "evidence_ids": refs,
            "confidence": _confidence_to_float(item.get("confidence", 0.6)),
            "involvement_indicator": _valid_choice(
                item.get("involvement_indicator"), {"High", "Moderate", "Low", "Unknown"}, "Unknown"
            ),
        })
    return _dedupe_dicts(persons, ("name", "evidence_ids"))[:_MAX_PERSONS]


def _normalise_unresolved(items: object) -> list[str]:
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return []
    clean = [_neutralise_text(item) for item in items]
    return _dedupe_list([item for item in clean if item])[:_MAX_UNRESOLVED]


# ── Utility helpers ────────────────────────────────────────────────────────────

def _valid_evidence_ids(collector_output: dict) -> set[str]:
    return set(_clean_refs(collector_output.get("evidence_ids", []), set()))


def _clean_refs(values: object, valid_evidence_ids: set[str]) -> list[str]:
    if isinstance(values, str):
        values = _EVIDENCE_ID_RE.findall(values)
    if not isinstance(values, list):
        return []
    refs = []
    for value in values:
        ref = _normalise_evidence_id(value)
        if not ref:
            continue
        if valid_evidence_ids and ref not in valid_evidence_ids:
            continue
        refs.append(ref)
    return _dedupe_list(refs)


def _refs_from_text(text: object, valid_evidence_ids: set[str]) -> list[str]:
    return _clean_refs(_BRACKETED_EVIDENCE_RE.findall(str(text or "")), valid_evidence_ids)


def _normalise_evidence_id(value: object) -> str:
    match = _EVIDENCE_ID_RE.search(str(value or "").upper())
    return match.group(0).upper() if match else ""


def _confidence_to_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    text = str(value or "").strip().lower()
    if text in {"high", "strong"}:
        return 0.85
    if text in {"medium", "moderate"}:
        return 0.65
    if text in {"low", "weak"}:
        return 0.4
    try:
        return max(0.0, min(1.0, float(text)))
    except ValueError:
        return 0.5


def _classification_prefix(classification: str, text: str) -> str:
    if classification in {"INFERRED", "UNCONFIRMED"}:
        return f"{classification.title()}: {text}"
    if classification == "CORROBORATED":
        return f"Corroborated: {text}"
    return text


def _append_refs(text: str, refs: list[str]) -> str:
    existing = set(_refs_from_text(text, set()))
    missing = [ref for ref in refs if ref not in existing]
    if not missing:
        return text
    return f"{text} {' '.join(f'[{ref}]' for ref in missing)}"


def _safe_person_note(value: object) -> str:
    text = _neutralise_text(value)
    if not text:
        return "Appears in the evidence and may warrant further review."
    return text


def _neutralise_text(value: object) -> str:
    text = _normalise_text(value)
    if not text:
        return ""
    text = _ACCUSATORY_RE.sub("is referenced in relation to", text)
    return _normalise_text(text)


def _contains_accusatory_claim(value: object) -> bool:
    return bool(_ACCUSATORY_RE.search(str(value or "")))


def _normalise_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _valid_choice(value: object, allowed: set[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _limit(values: object, limit: int) -> list:
    return values[:limit] if isinstance(values, list) else []


def _dedupe_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _dedupe_dicts(values: list[dict], keys: tuple[str, ...]) -> list[dict]:
    seen: set[tuple] = set()
    output = []
    for value in values:
        identity = tuple(json.dumps(value.get(key), sort_keys=True) for key in keys)
        if identity in seen:
            continue
        seen.add(identity)
        output.append(value)
    return output


# ── DB helpers ─────────────────────────────────────────────────────────────────

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
