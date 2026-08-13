"""
agents/evidence_linker.py
--------------------------
Agent 04 — Evidence Linker
Finds relationships between evidence items:
shared phone numbers, matching timestamps, common locations, etc.
"""

import asyncio
from typing import Callable, Awaitable

from app.database import collections
from app.services.llm_service import chat_completion_json
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

_SYSTEM_PROMPT = """You are a forensic link analyst.
Find relationships and connections between the provided evidence items.

Look for:
- Shared phone numbers, emails or contact information
- Matching or overlapping timestamps
- Same locations or places
- Common people or organisations
- Consistent or contradictory statements
- Corroborating details across multiple items

Return ONLY valid JSON:
{
  "links": [
    {
      "a": "E-001",
      "b": "E-003",
      "reason": "Both contain the phone number +44 7700 900123",
      "strength": "strong" | "moderate" | "weak"
    }
  ],
  "strong_links": 0,
  "weak_links": 0
}

Only report connections with clear evidence. Do not guess."""


async def run(investigation_id: str, emit: Emitter, analyzer_output: dict) -> dict:
    """Run the Evidence Linker agent."""
    agent_id = "linker"
    await _set_status(investigation_id, agent_id, "running")
    lines = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    await add_line("scan", "Cross-referencing evidence items for shared attributes...")

    # Build evidence summary for the LLM
    evidence_docs = await collections.evidence().find(
        {"investigation_id": investigation_id}
    ).to_list(length=None)

    summaries = []
    for ev in evidence_docs:
        content = ev.get("content", "")
        if content:
            summaries.append(f"[{ev['evidence_id']}] {ev['filename']}:\n{content[:1500]}")

    combined = "\n\n".join(summaries) if summaries else "No evidence content."

    try:
        result = await chat_completion_json(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Evidence items:\n\n{combined[:8000]}"},
            ],
            temperature=0.1,
        )
    except Exception as e:
        await add_line("warn", f"Link analysis failed: {str(e)[:80]}")
        result = {"links": [], "strong_links": 0, "weak_links": 0}

    links = result.get("links", [])
    await asyncio.sleep(0.2)

    strong_count = 0
    weak_count = 0

    for link in links:
        strength = link.get("strength", "moderate")
        if strength == "strong":
            strong_count += 1
            await add_line("ok", f"{link['a']} ↔ {link['b']}: {link['reason']}")
        elif strength == "weak":
            weak_count += 1
            await add_line("warn", f"Weak link — {link['a']} ↔ {link['b']}: {link['reason']}")
        else:
            await add_line("ok", f"{link['a']} ↔ {link['b']}: {link['reason']}")

    result["strong_links"] = strong_count
    result["weak_links"] = weak_count

    summary = [
        f"{strong_count} strong link{'s' if strong_count != 1 else ''}",
        f"{weak_count} weak link{'s' if weak_count != 1 else ''}",
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
