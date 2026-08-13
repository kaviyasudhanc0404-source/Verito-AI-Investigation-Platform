"""
agents/timeline_builder.py
---------------------------
Agent 03 — Timeline Builder
Reconstructs a chronological timeline from extracted evidence.
Classifies each event as FACT, INFERRED, or UNCONFIRMED.
"""

import asyncio
from typing import Callable, Awaitable

from app.database import collections
from app.services.llm_service import chat_completion_json
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

_SYSTEM_PROMPT = """You are a forensic timeline analyst.
Build a chronological timeline of events from the evidence.

For each event, classify it as:
- FACT: directly stated in evidence with a clear timestamp
- INFERRED: suggested by evidence but not explicitly stated
- UNCONFIRMED: mentioned but without verifiable support

Return ONLY valid JSON:
{
  "events": [
    {
      "time": "date/time string or 'Unknown'",
      "event": "brief description of what happened",
      "refs": ["E-001", "E-002"],
      "classification": "FACT" | "INFERRED" | "UNCONFIRMED"
    }
  ],
  "time_window": "e.g. '14 March 21:14 – 22:11' or 'Unknown'"
}

Only include events with clear temporal anchors. Sort chronologically."""


async def run(investigation_id: str, emit: Emitter, analyzer_output: dict) -> dict:
    """Run the Timeline Builder agent."""
    agent_id = "timeline"
    await _set_status(investigation_id, agent_id, "running")
    lines = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    await add_line("info", "Reconstructing chronological events from evidence...")

    # Fetch all evidence content
    evidence_docs = await collections.evidence().find(
        {"investigation_id": investigation_id}
    ).to_list(length=None)

    # Build a combined evidence context for timeline extraction
    context_parts = []
    for ev in evidence_docs:
        content = ev.get("content", "")
        if content:
            context_parts.append(f"[{ev['evidence_id']}] {ev['filename']}:\n{content[:2000]}")

    context = "\n\n".join(context_parts) if context_parts else "No evidence content available."

    # Include known dates from analyzer output
    known_dates = analyzer_output.get("dates", [])
    if known_dates:
        await add_line("scan", f"Known dates from analysis: {', '.join(known_dates[:5])}")

    try:
        result = await chat_completion_json(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Known dates from analysis: {', '.join(known_dates)}\n\n"
                        f"Evidence:\n{context[:8000]}"
                    ),
                },
            ],
            temperature=0.1,
        )
    except Exception as e:
        await add_line("warn", f"Timeline extraction failed: {str(e)[:80]}")
        result = {"events": [], "time_window": "Unknown"}

    events = result.get("events", [])
    time_window = result.get("time_window", "Unknown")

    await asyncio.sleep(0.2)

    for event in events:
        classification = event.get("classification", "FACT")
        refs_str = ", ".join(event.get("refs", []))
        await add_line(
            "ok" if classification == "FACT" else "scan",
            f"{event.get('time', '?')} — {event.get('event', '')} [{classification}]" +
            (f" ({refs_str})" if refs_str else ""),
        )

    await add_line("ok", f"{len(events)} timestamped events sequenced")

    summary = [
        f"{len(events)} events sequenced",
        f"Window: {time_window}" if time_window != "Unknown" else "Timeline window unknown",
    ]

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
