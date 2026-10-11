"""
workflows/pipeline.py
---------------------
Runs the seven-agent investigation pipeline and fans its live events out to any
number of SSE subscribers.

    collector → analyzer → timeline → linker → validator → subject → report

  • One run per investigation: a page refresh, reconnect or second tab attaches
    to the run in progress (and receives the events so far) instead of starting
    a new one.
  • If a step fails or is blocked, later steps are marked as skipped.
  • Every stage's status, duration, counts, fallbacks and LLM usage are recorded
    as diagnostics and attached to the saved report.
  • At the end the investigation status is set to "complete" or "failed", and an
    untitled case takes its title from the report.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.agents import (
    evidence_analyzer, evidence_collector, evidence_linker, finding_validator,
    report_generator, subject_analyzer, timeline_builder,
)
from app.agents.base import AgentResult, AgentRun, PipelineContext, reset_agent_runs
from app.services import investigation_service, report_service
from app.services.llm_service import track_usage

logger = logging.getLogger(__name__)

STAGES = (
    ("collector", "Evidence Collector", evidence_collector.run),
    ("analyzer", "Evidence Analyzer", evidence_analyzer.run),
    ("timeline", "Timeline Builder", timeline_builder.run),
    ("linker", "Evidence Linker", evidence_linker.run),
    ("validator", "Finding Validator", finding_validator.run),
    ("subject", "Subject Analyzer", subject_analyzer.run),
    ("report", "Report Generator", report_generator.run),
)
UNTITLED = "Untitled case"


async def run_investigation_pipeline(investigation_id: str, user_id: str, prompt: str, emit) -> str:
    """Run every stage in order. Returns the final investigation status."""
    ctx = PipelineContext(investigation_id, user_id, prompt, emit)
    await reset_agent_runs(investigation_id)
    await investigation_service.update_investigation_status(investigation_id, "processing")

    skip_reason = ""
    for agent_id, name, step in STAGES:
        agent = AgentRun(ctx, agent_id)
        if skip_reason:
            await agent.finish(AgentResult({}, ["Skipped"], "blocked", skip_reason))
            continue
        await agent.start()
        started = time.perf_counter()
        with track_usage() as usage:
            try:
                result = await step(ctx, agent)
            except Exception as exc:
                logger.exception("%s failed for %s", name, investigation_id)
                result = AgentResult({}, ["Failed"], "failed", f"{name} stopped unexpectedly: {exc}")
                await agent.log("error", result.reason)
        await agent.finish(result)
        ctx.outputs[agent_id] = result.output
        ctx.diagnostics[agent_id] = {
            "name": name, "status": result.status, "reason": result.reason,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "warnings": agent.warnings, "fallbacks": agent.fallbacks, "counts": result.counts, "llm": usage.to_dict(),
        }
        if result.status in {"failed", "blocked"}:
            skip_reason = f"Skipped because the {name} step did not complete."

    status = "failed" if skip_reason else "complete"
    if "report" in ctx.diagnostics and ctx.diagnostics["report"]["status"] not in {"failed", "blocked"}:
        await report_service.update_report(investigation_id, diagnostics=ctx.diagnostics)
    await investigation_service.update_investigation_status(investigation_id, status)
    title = ctx.outputs.get("report", {}).get("title")
    if title:
        inv = await investigation_service.get_investigation(investigation_id)
        if inv and inv.get("title", "").strip() in {"", UNTITLED}:
            await investigation_service.update_investigation(investigation_id, title=title)
    await emit("investigation_completed", {"investigation_id": investigation_id, "status": status})
    return status


# ── Live run registry (one run per investigation, many subscribers) ──────────

@dataclass
class _LiveRun:
    task: asyncio.Task | None = None
    history: list[tuple[str, dict]] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)

    async def emit(self, event: str, payload: dict) -> None:
        self.history.append((event, payload))
        for queue in self.subscribers:
            queue.put_nowait((event, payload))

_LIVE_RUNS: dict[str, _LiveRun] = {}


def _finished(investigation_id: str, live: _LiveRun, task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.error("Pipeline crashed for %s", investigation_id, exc_info=task.exception())
    for queue in live.subscribers:
        queue.put_nowait(None)
    if _LIVE_RUNS.get(investigation_id) is live:
        del _LIVE_RUNS[investigation_id]


def subscribe(investigation_id: str, user_id: str, prompt: str) -> asyncio.Queue:
    """Attach to the investigation's live run, starting one if none is active.

    The queue first receives every event emitted so far, then live events, then
    None when the run ends.
    """
    live = _LIVE_RUNS.get(investigation_id)
    if live is None or live.task is None or live.task.done():
        live = _LiveRun()
        _LIVE_RUNS[investigation_id] = live
        live.task = asyncio.create_task(run_investigation_pipeline(investigation_id, user_id, prompt, live.emit))
        live.task.add_done_callback(lambda task: _finished(investigation_id, live, task))

    queue: asyncio.Queue = asyncio.Queue()
    for item in live.history:
        queue.put_nowait(item)
    live.subscribers.add(queue)
    return queue


def unsubscribe(investigation_id: str, queue: asyncio.Queue) -> None:
    live = _LIVE_RUNS.get(investigation_id)
    if live:
        live.subscribers.discard(queue)


def is_running(investigation_id: str) -> bool:
    live = _LIVE_RUNS.get(investigation_id)
    return bool(live and live.task and not live.task.done())
