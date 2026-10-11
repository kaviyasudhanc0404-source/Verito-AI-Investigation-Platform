"""
agents/base.py
--------------
Shared plumbing for the seven investigation agents.

  PipelineContext — what every agent receives: ids, the investigation question,
                    earlier agents' outputs, the shared Case model, the reconciliation
                    observations and per-stage diagnostics.
  AgentRun        — one agent's live run: status, activity lines and final
                    output, persisted to MongoDB and streamed over SSE.
  AgentResult     — what an agent returns: output, summary chips, status and counts.

Statuses (most severe last):
  idle (pending) · running · done · completed_with_warnings · completed_with_fallback · failed · blocked

SSE events emitted (consumed by the frontend):
  agent_started, agent_activity, agent_completed, agent_completed_with_warnings,
  agent_completed_with_fallback, agent_failed, agent_blocked
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.database import collections
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

AGENT_IDS = ("collector", "analyzer", "timeline", "linker", "validator", "subject", "report")

_EVENT_BY_STATUS = {
    "done": "agent_completed",
    "completed_with_warnings": "agent_completed_with_warnings",
    "completed_with_fallback": "agent_completed_with_fallback",
    "failed": "agent_failed",
    "blocked": "agent_blocked",
}


@dataclass
class AgentResult:
    output: dict
    summary: list[str]
    status: str = "done"
    reason: str = ""                                   # shown for failed / blocked
    counts: dict[str, int] = field(default_factory=dict)  # processed / skipped / … for diagnostics


@dataclass
class PipelineContext:
    investigation_id: str
    user_id: str
    prompt: str
    emit: Emitter
    outputs: dict[str, dict] = field(default_factory=dict)
    case: object | None = None          # app.services.case_model.Case, set by the analyzer
    observations: list = field(default_factory=list)   # reconciliation Observations, set by the linker
    diagnostics: dict[str, dict] = field(default_factory=dict)


class AgentRun:
    def __init__(self, ctx: PipelineContext, agent_id: str) -> None:
        self.ctx = ctx
        self.agent_id = agent_id
        self.warnings = 0
        self.fallbacks = 0

    @property
    def _filter(self) -> dict:
        return {"investigation_id": self.ctx.investigation_id, "agent_id": self.agent_id}

    @property
    def status(self) -> str:
        """Completion status implied by what was logged so far."""
        if self.fallbacks:
            return "completed_with_fallback"
        return "completed_with_warnings" if self.warnings else "done"

    async def start(self) -> None:
        await _update(self._filter, {
            "status": "running", "lines": [], "summary": [], "output": {}, "updated_at": utcnow(),
        })
        await self.ctx.emit("agent_started", {"agent_id": self.agent_id})

    async def log(self, kind: str, text: str) -> None:
        """Record one activity line: kind is info | scan | ok | warn | error."""
        if kind in {"warn", "error"}:
            self.warnings += 1
        line = {"kind": kind, "text": text}
        await collections.agent_runs().update_one(
            self._filter, {"$push": {"lines": line}, "$set": {"updated_at": utcnow()}}
        )
        await self.ctx.emit("agent_activity", {"agent_id": self.agent_id, "line": line})

    async def fallback(self, text: str, units: int = 1) -> None:
        """Log that `units` pieces of work used the rule-based fallback instead of the LLM."""
        self.fallbacks += units
        await self.log("warn", text)

    async def finish(self, result: AgentResult) -> None:
        await _update(self._filter, {
            "status": result.status, "summary": result.summary, "output": result.output, "updated_at": utcnow(),
        })
        payload: dict = {"agent_id": self.agent_id}
        if result.status == "failed":
            payload["error"] = result.reason or "Agent failed."
        if result.status == "blocked":
            payload["reason"] = result.reason or "Blocked by an earlier step."
        await self.ctx.emit(_EVENT_BY_STATUS.get(result.status, "agent_completed"), payload)


async def reset_agent_runs(investigation_id: str) -> None:
    """Create (or reset to idle) the agent_run documents for a fresh run."""
    for agent_id in AGENT_IDS:
        await _update(
            {"investigation_id": investigation_id, "agent_id": agent_id},
            {"status": "idle", "lines": [], "summary": [], "output": {}, "updated_at": utcnow()},
            insert={"_id": f"{investigation_id}_{agent_id}"},
        )


async def _update(query: dict, values: dict, insert: dict | None = None) -> None:
    update: dict = {"$set": values}
    if insert:
        update["$setOnInsert"] = insert
    await collections.agent_runs().update_one(query, update, upsert=True)
