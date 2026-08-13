"""
agents/report_generator.py
---------------------------
Agent 06 — Report Generator
Assembles the final case report from all previous agent outputs.
Generates:
  - Case Summary
  - Key Findings (with evidence refs)
  - Timeline
  - Evidence Connections
  - Persons of Interest
  - Unresolved Questions
"""

import asyncio
from typing import Callable, Awaitable

from app.database import collections
from app.services.llm_service import chat_completion_json
from app.services.report_service import save_report
from app.services.investigation_service import update_investigation_status
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

_SYSTEM_PROMPT = """You are a senior forensic investigator writing a final case report.
Synthesise all agent outputs into a coherent investigation report.

Return ONLY valid JSON in exactly this format:
{
  "summary": "2-4 sentence case summary",
  "findings": [
    "Finding 1 with reference to [E-001]",
    "Finding 2 with reference to [E-003]"
  ],
  "timeline": [
    {"time": "21:14", "event": "Message sent", "refs": ["E-002"]}
  ],
  "connections": [
    {"a": "E-001", "b": "E-005", "reason": "Shared phone number"}
  ],
  "persons": [
    {"name": "Name", "note": "description with involvement indicator"}
  ],
  "unresolved": [
    "Unresolved question or gap in evidence"
  ]
}

Reference evidence by ID (e.g. [E-001]) in findings and summary.
Do not determine guilt. Report only what the evidence supports."""


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
    lines = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    await add_line("info", "Assembling case summary from all agent outputs...")
    await add_line("info", "Compiling timeline & connections...")

    # Build the synthesis prompt with all agent outputs
    synthesis_context = f"""
INVESTIGATION QUERY: {investigation_prompt}

EVIDENCE COLLECTOR OUTPUT:
- Items indexed: {collector_output.get('items_indexed', 0)}
- Evidence IDs: {', '.join(collector_output.get('evidence_ids', []))}

ANALYZER FINDINGS:
- People: {', '.join(analyzer_output.get('people', [])[:10])}
- Locations: {', '.join(analyzer_output.get('locations', [])[:10])}
- Dates: {', '.join(analyzer_output.get('dates', [])[:10])}
- Key findings: {'; '.join(analyzer_output.get('key_findings', [])[:5])}

TIMELINE:
{_format_events(timeline_output.get('events', []))}

EVIDENCE LINKS:
{_format_links(linker_output.get('links', []))}

PERSONS OF INTEREST:
{_format_persons(subject_output.get('persons', []))}
"""

    try:
        report_data = await chat_completion_json(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_context[:10000]},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
    except Exception as e:
        await add_line("warn", f"Report generation failed: {str(e)[:80]}")
        # Fallback with available data
        report_data = _build_fallback_report(
            analyzer_output, timeline_output, linker_output, subject_output
        )

    await asyncio.sleep(0.3)
    await add_line("ok", "Draft report generated")
    await add_line("ok", "Findings ready for review")

    # Save the report to MongoDB
    await save_report(investigation_id, report_data)

    # Mark the investigation as complete
    await update_investigation_status(investigation_id, "complete")

    summary = ["Report ready"]
    await _complete(investigation_id, agent_id, lines, summary, report_data)
    await emit("agent_completed", {"agent_id": agent_id})

    return report_data


def _format_events(events: list[dict]) -> str:
    if not events:
        return "No timestamped events found."
    return "\n".join(
        f"- {e.get('time', '?')}: {e.get('event', '')} (refs: {', '.join(e.get('refs', []))})"
        for e in events[:10]
    )


def _format_links(links: list[dict]) -> str:
    if not links:
        return "No evidence links found."
    return "\n".join(
        f"- {l.get('a', '?')} ↔ {l.get('b', '?')}: {l.get('reason', '')}"
        for l in links[:10]
    )


def _format_persons(persons: list[dict]) -> str:
    if not persons:
        return "No persons of interest identified."
    return "\n".join(
        f"- {p.get('name', '?')}: {p.get('note', '')} [Involvement: {p.get('involvement_indicator', '?')}]"
        for p in persons[:10]
    )


def _build_fallback_report(
    analyzer_output: dict,
    timeline_output: dict,
    linker_output: dict,
    subject_output: dict,
) -> dict:
    """Build a minimal report from agent outputs if LLM synthesis fails."""
    events = timeline_output.get("events", [])
    links = linker_output.get("links", [])
    persons = subject_output.get("persons", [])

    return {
        "summary": "Investigation analysis complete. Review findings below.",
        "findings": analyzer_output.get("key_findings", ["Analysis complete."]),
        "timeline": [
            {"time": e.get("time", "?"), "event": e.get("event", ""), "refs": e.get("refs", [])}
            for e in events[:8]
        ],
        "connections": [
            {"a": l.get("a", ""), "b": l.get("b", ""), "reason": l.get("reason", "")}
            for l in links[:6]
        ],
        "persons": [
            {"name": p.get("name", "Unknown"), "note": p.get("note", "")}
            for p in persons[:5]
        ],
        "unresolved": ["Manual review may be required for full analysis."],
    }


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
    investigation_id: str, agent_id: str,
    lines: list[dict], summary: list[str], output: dict,
) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {"$set": {"status": "done", "summary": summary, "output": output, "updated_at": utcnow()}},
    )
