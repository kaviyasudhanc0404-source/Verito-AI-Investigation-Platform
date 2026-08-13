"""
workflows/investigation_graph.py
---------------------------------
LangGraph StateGraph that orchestrates the six-agent investigation pipeline.

Flow:
  collector → analyzer → timeline → linker → subject → report

The graph is compiled once and called with investigation-specific state.
Each node wraps an agent module and passes an SSE emit callback for live updates.
"""

import asyncio
from typing import AsyncIterator, Callable, Awaitable

from langgraph.graph import StateGraph, END

from app.workflows.investigation_state import InvestigationState
from app.agents import (
    evidence_collector,
    evidence_analyzer,
    timeline_builder,
    evidence_linker,
    subject_analyzer,
    report_generator,
)
from app.database import collections
from app.utils.datetime_utils import utcnow

# Type for the SSE emit callback
Emitter = Callable[[str, dict], Awaitable[None]]

# ── Agent IDs (must match frontend strings exactly) ───────────────────────────
AGENT_IDS = ["collector", "analyzer", "timeline", "linker", "subject", "report"]


def _initialise_agent_runs(investigation_id: str) -> list[dict]:
    """Build the initial idle agent_run documents."""
    return [
        {
            "_id": f"{investigation_id}_{agent_id}",
            "investigation_id": investigation_id,
            "agent_id": agent_id,
            "status": "idle",
            "lines": [],
            "summary": [],
            "output": {},
            "updated_at": utcnow(),
        }
        for agent_id in AGENT_IDS
    ]


async def setup_agent_runs(investigation_id: str) -> None:
    """
    Insert idle agent_run documents into MongoDB.
    Safe to call multiple times — uses upsert.
    """
    for agent_id in AGENT_IDS:
        await collections.agent_runs().update_one(
            {"investigation_id": investigation_id, "agent_id": agent_id},
            {
                "$setOnInsert": {
                    "_id": f"{investigation_id}_{agent_id}",
                    "investigation_id": investigation_id,
                    "agent_id": agent_id,
                    "status": "idle",
                    "lines": [],
                    "summary": [],
                    "output": {},
                    "updated_at": utcnow(),
                }
            },
            upsert=True,
        )


async def run_investigation_pipeline(
    investigation_id: str,
    user_id: str,
    investigation_prompt: str,
    emit: Emitter,
) -> None:
    """
    Execute the full six-agent pipeline for an investigation.
    Each agent emits SSE events via the `emit` callback.

    This function is designed to run as a background task.
    All progress is persisted to MongoDB so it's recoverable on page refresh.
    """

    # Initialise agent_run documents in MongoDB
    await setup_agent_runs(investigation_id)

    # Shared state
    state: InvestigationState = {
        "investigation_id": investigation_id,
        "user_id": user_id,
        "investigation_prompt": investigation_prompt,
        "collector_output": {},
        "analyzer_output": {},
        "timeline_output": {},
        "linker_output": {},
        "subject_output": {},
        "report_output": {},
        "error": None,
    }

    # ── Node wrappers ──────────────────────────────────────────────────────────

    async def node_collector(s: InvestigationState) -> InvestigationState:
        await emit("agent_started", {"agent_id": "collector"})
        try:
            output = await evidence_collector.run(s["investigation_id"], emit)
            return {**s, "collector_output": output}
        except Exception as e:
            await emit("agent_failed", {"agent_id": "collector", "error": str(e)})
            await _mark_failed(s["investigation_id"], "collector")
            return {**s, "error": str(e)}

    async def node_analyzer(s: InvestigationState) -> InvestigationState:
        if s.get("error"):
            return s
        await emit("agent_started", {"agent_id": "analyzer"})
        try:
            output = await evidence_analyzer.run(s["investigation_id"], emit)
            return {**s, "analyzer_output": output}
        except Exception as e:
            await emit("agent_failed", {"agent_id": "analyzer", "error": str(e)})
            await _mark_failed(s["investigation_id"], "analyzer")
            return {**s, "error": str(e)}

    async def node_timeline(s: InvestigationState) -> InvestigationState:
        if s.get("error"):
            return s
        await emit("agent_started", {"agent_id": "timeline"})
        try:
            output = await timeline_builder.run(
                s["investigation_id"], emit, s["analyzer_output"]
            )
            return {**s, "timeline_output": output}
        except Exception as e:
            await emit("agent_failed", {"agent_id": "timeline", "error": str(e)})
            await _mark_failed(s["investigation_id"], "timeline")
            return {**s, "error": str(e)}

    async def node_linker(s: InvestigationState) -> InvestigationState:
        if s.get("error"):
            return s
        await emit("agent_started", {"agent_id": "linker"})
        try:
            output = await evidence_linker.run(
                s["investigation_id"], emit, s["analyzer_output"]
            )
            return {**s, "linker_output": output}
        except Exception as e:
            await emit("agent_failed", {"agent_id": "linker", "error": str(e)})
            await _mark_failed(s["investigation_id"], "linker")
            return {**s, "error": str(e)}

    async def node_subject(s: InvestigationState) -> InvestigationState:
        if s.get("error"):
            return s
        await emit("agent_started", {"agent_id": "subject"})
        try:
            output = await subject_analyzer.run(
                s["investigation_id"], emit, s["analyzer_output"]
            )
            return {**s, "subject_output": output}
        except Exception as e:
            await emit("agent_failed", {"agent_id": "subject", "error": str(e)})
            await _mark_failed(s["investigation_id"], "subject")
            return {**s, "error": str(e)}

    async def node_report(s: InvestigationState) -> InvestigationState:
        if s.get("error"):
            return s
        await emit("agent_started", {"agent_id": "report"})
        try:
            output = await report_generator.run(
                investigation_id=s["investigation_id"],
                emit=emit,
                collector_output=s["collector_output"],
                analyzer_output=s["analyzer_output"],
                timeline_output=s["timeline_output"],
                linker_output=s["linker_output"],
                subject_output=s["subject_output"],
                investigation_prompt=s["investigation_prompt"],
            )
            return {**s, "report_output": output}
        except Exception as e:
            await emit("agent_failed", {"agent_id": "report", "error": str(e)})
            await _mark_failed(s["investigation_id"], "report")
            return {**s, "error": str(e)}

    # ── Build and run the LangGraph ────────────────────────────────────────────
    graph = StateGraph(InvestigationState)

    graph.add_node("collector", node_collector)
    graph.add_node("analyzer", node_analyzer)
    graph.add_node("timeline", node_timeline)
    graph.add_node("linker", node_linker)
    graph.add_node("subject", node_subject)
    graph.add_node("report", node_report)

    graph.set_entry_point("collector")
    graph.add_edge("collector", "analyzer")
    graph.add_edge("analyzer", "timeline")
    graph.add_edge("timeline", "linker")
    graph.add_edge("linker", "subject")
    graph.add_edge("subject", "report")
    graph.add_edge("report", END)

    compiled = graph.compile()
    await compiled.ainvoke(state)

    # Signal completion
    await emit("investigation_completed", {"investigation_id": investigation_id})


def build_graph() -> object:
    """
    Compile the graph without running it.
    Used for import-time validation — e.g. in tests or startup checks.
    """
    graph = StateGraph(InvestigationState)
    graph.add_node("collector", lambda s: s)
    graph.add_node("analyzer", lambda s: s)
    graph.add_node("timeline", lambda s: s)
    graph.add_node("linker", lambda s: s)
    graph.add_node("subject", lambda s: s)
    graph.add_node("report", lambda s: s)
    graph.set_entry_point("collector")
    graph.add_edge("collector", "analyzer")
    graph.add_edge("analyzer", "timeline")
    graph.add_edge("timeline", "linker")
    graph.add_edge("linker", "subject")
    graph.add_edge("subject", "report")
    graph.add_edge("report", END)
    return graph.compile()


async def _mark_failed(investigation_id: str, agent_id: str) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {"$set": {"status": "failed", "updated_at": utcnow()}},
    )
