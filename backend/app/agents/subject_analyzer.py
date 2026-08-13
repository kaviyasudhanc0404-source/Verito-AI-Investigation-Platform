"""
agents/subject_analyzer.py
---------------------------
Agent 05 — Subject Analyzer
Profiles Persons of Interest from the evidence.
Uses "Involvement Indicator" — never "Guilt Score".
Does not determine whether a person is guilty.
"""

import asyncio
from typing import Callable, Awaitable

from app.database import collections
from app.services.llm_service import chat_completion_json
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

_SYSTEM_PROMPT = """You are a forensic subject analyst.
Profile each person of interest identified in the evidence.

Important rules:
- Use "Involvement Indicator" to describe how frequently or significantly a person appears
- NEVER use "guilt score" or suggest that any person is guilty
- Only report what the evidence shows
- Note the number of mentions and locations connected to each person

Return ONLY valid JSON:
{
  "persons": [
    {
      "name": "Full Name",
      "mentions": 3,
      "locations": ["Location A", "Location B"],
      "evidence_refs": ["E-001", "E-003"],
      "involvement_indicator": "High | Moderate | Low",
      "note": "brief description of their presence in the evidence"
    }
  ]
}"""


async def run(
    investigation_id: str,
    emit: Emitter,
    analyzer_output: dict,
) -> dict:
    """Run the Subject Analyzer agent."""
    agent_id = "subject"
    await _set_status(investigation_id, agent_id, "running")
    lines = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    people = analyzer_output.get("people", [])
    await add_line("info", f"Building profiles for {len(people)} identified person(s)...")

    if not people:
        await add_line("warn", "No persons identified in evidence — skipping subject analysis.")
        output = {"persons": []}
        await _complete(investigation_id, agent_id, lines, ["No subjects identified"], output)
        await emit("agent_completed", {"agent_id": agent_id})
        return output

    # Build evidence context
    evidence_docs = await collections.evidence().find(
        {"investigation_id": investigation_id}
    ).to_list(length=None)

    evidence_context = "\n\n".join(
        f"[{ev['evidence_id']}] {ev['filename']}:\n{(ev.get('content') or '')[:1500]}"
        for ev in evidence_docs
    )

    try:
        result = await chat_completion_json(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Known persons of interest: {', '.join(people)}\n\n"
                        f"Evidence:\n{evidence_context[:8000]}"
                    ),
                },
            ],
            temperature=0.1,
        )
    except Exception as e:
        await add_line("warn", f"Subject analysis failed: {str(e)[:80]}")
        result = {"persons": []}

    persons = result.get("persons", [])
    await asyncio.sleep(0.2)

    for person in persons:
        locs = ", ".join(person.get("locations", []))
        indicator = person.get("involvement_indicator", "Unknown")
        await add_line(
            "ok",
            f"{person['name']} — {person.get('mentions', '?')} mention(s)"
            + (f", {locs}" if locs else "")
            + f" [Involvement: {indicator}]",
        )

    summary = [f"{len(persons)} person(s) of interest"]
    await _complete(investigation_id, agent_id, lines, summary, result)
    await emit("agent_completed", {"agent_id": agent_id})

    return result


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
