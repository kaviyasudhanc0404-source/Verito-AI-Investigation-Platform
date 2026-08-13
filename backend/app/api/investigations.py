"""
api/investigations.py
----------------------
Investigation CRUD + the SSE streaming endpoint that runs the agent pipeline.
"""

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse

from app.core.exceptions import NotFoundException, ForbiddenException
from app.database import collections
from app.dependencies.auth import get_current_user
from app.schemas.agent import AgentRunResponse, AgentLineResponse
from app.schemas.chat import ChatMessageResponse
from app.schemas.evidence import EvidenceItemResponse
from app.schemas.investigation import (
    CreateInvestigationRequest,
    InvestigationListItem,
    InvestigationResponse,
    UpdateInvestigationRequest,
)
from app.schemas.report import (
    ReportResponse,
    TimelineEntryResponse,
    ConnectionEntryResponse,
    PersonEntryResponse,
)
from app.services import investigation_service, vector_store
from app.services.evidence_service import get_evidence_for_investigation
from app.services.report_service import get_report
from app.workflows.investigation_graph import run_investigation_pipeline

router = APIRouter(prefix="/investigations", tags=["Investigations"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _evidence_to_schema(ev: dict) -> EvidenceItemResponse:
    return EvidenceItemResponse(
        id=ev["evidence_id"],
        name=ev["filename"],
        kind=ev["kind"],
        size=ev.get("size_bytes"),
    )


def _run_to_schema(run: dict) -> AgentRunResponse:
    return AgentRunResponse(
        id=run["agent_id"],
        status=run["status"],
        lines=[AgentLineResponse(**l) for l in run.get("lines", [])],
        summary=run.get("summary", []),
    )


def _report_to_schema(report: dict) -> ReportResponse:
    return ReportResponse(
        summary=report.get("summary", ""),
        findings=report.get("findings", []),
        timeline=[TimelineEntryResponse(**t) for t in report.get("timeline", [])],
        connections=[ConnectionEntryResponse(**c) for c in report.get("connections", [])],
        persons=[PersonEntryResponse(**p) for p in report.get("persons", [])],
        unresolved=report.get("unresolved", []),
    )


async def _build_investigation_response(
    inv: dict,
    user_id: str,
) -> InvestigationResponse:
    """Build a full InvestigationResponse with all nested data."""
    evidence_docs = await get_evidence_for_investigation(inv["_id"])
    agent_run_docs = await collections.agent_runs().find(
        {"investigation_id": inv["_id"]},
        sort=[("agent_id", 1)],
    ).to_list(length=None)

    # Sort agent runs in the correct pipeline order
    order = ["collector", "analyzer", "timeline", "linker", "subject", "report"]
    run_map = {r["agent_id"]: r for r in agent_run_docs}
    ordered_runs = [run_map[aid] for aid in order if aid in run_map]

    report_doc = await get_report(inv["_id"])

    chat_docs = await collections.chat_messages().find(
        {"investigation_id": inv["_id"]},
        sort=[("created_at", 1)],
    ).to_list(length=None)

    return InvestigationResponse(
        id=inv["_id"],
        title=inv["title"],
        prompt=inv["prompt"],
        status=inv["status"],
        pinned=inv.get("pinned", False),
        created_at=inv["created_at"],
        evidence=[_evidence_to_schema(e) for e in evidence_docs],
        runs=[_run_to_schema(r) for r in ordered_runs],
        report=_report_to_schema(report_doc) if report_doc else None,
        chat=[
            ChatMessageResponse(
                id=m["_id"],
                role=m["role"],
                text=m["text"],
                refs=m.get("refs", []),
            )
            for m in chat_docs
        ],
    )


async def _check_ownership(investigation_id: str, user_id: str) -> dict:
    """Verify investigation exists and belongs to the current user."""
    inv = await investigation_service.get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != user_id:
        raise ForbiddenException("You do not have access to this investigation.")
    return inv


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[InvestigationListItem])
async def list_investigations(
    current_user: dict = Depends(get_current_user),
) -> list[InvestigationListItem]:
    """Return all investigations belonging to the current user."""
    inv_docs = await investigation_service.list_user_investigations(current_user["_id"])
    return [
        InvestigationListItem(
            id=inv["_id"],
            title=inv["title"],
            status=inv["status"],
            pinned=inv.get("pinned", False),
            created_at=inv["created_at"],
        )
        for inv in inv_docs
    ]


@router.post("", response_model=InvestigationResponse, status_code=201)
async def create_investigation(
    body: CreateInvestigationRequest,
    current_user: dict = Depends(get_current_user),
) -> InvestigationResponse:
    """Create a new investigation."""
    inv = await investigation_service.create_investigation(
        user_id=current_user["_id"],
        title=body.title,
        prompt=body.prompt,
    )
    return await _build_investigation_response(inv, current_user["_id"])


@router.get("/{investigation_id}", response_model=InvestigationResponse)
async def get_investigation(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> InvestigationResponse:
    """Get a single investigation with all agent runs, evidence and report."""
    inv = await _check_ownership(investigation_id, current_user["_id"])
    return await _build_investigation_response(inv, current_user["_id"])


@router.patch("/{investigation_id}", response_model=InvestigationListItem)
async def update_investigation(
    investigation_id: str,
    body: UpdateInvestigationRequest,
    current_user: dict = Depends(get_current_user),
) -> InvestigationListItem:
    """Rename and/or pin an investigation owned by the current user."""
    await _check_ownership(investigation_id, current_user["_id"])

    title = body.title.strip() if body.title is not None else None
    if body.title is not None and not title:
        title = "Untitled case"

    inv = await investigation_service.update_investigation(
        investigation_id,
        title=title,
        pinned=body.pinned,
    )
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")

    return InvestigationListItem(
        id=inv["_id"],
        title=inv["title"],
        status=inv["status"],
        pinned=inv.get("pinned", False),
        created_at=inv["created_at"],
    )


@router.delete("/{investigation_id}", status_code=204)
async def delete_investigation(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> None:
    """Delete an investigation and all its related data."""
    inv = await _check_ownership(investigation_id, current_user["_id"])

    # Delete vectors from Qdrant Cloud before Mongo metadata is removed.
    try:
        await asyncio.to_thread(
            vector_store.delete_investigation_chunks,
            investigation_id,
            inv["user_id"],
        )
    except Exception:
        pass  # Non-fatal if Qdrant cleanup fails

    await investigation_service.delete_investigation(investigation_id, current_user["_id"])


@router.get("/{investigation_id}/stream")
async def stream_investigation(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """
    SSE endpoint — starts the LangGraph agent pipeline for the investigation.
    The client receives real-time events as each agent processes evidence.
    If the investigation is already complete, returns the final state immediately.
    """
    inv = await _check_ownership(investigation_id, current_user["_id"])

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: str, payload: dict) -> None:
            await queue.put((event, payload))

        # Start the pipeline as a concurrent task
        pipeline_task = asyncio.create_task(
            run_investigation_pipeline(
                investigation_id=investigation_id,
                user_id=current_user["_id"],
                investigation_prompt=inv["prompt"],
                emit=emit,
            )
        )

        # Signal end when pipeline is done
        async def signal_end():
            await pipeline_task
            await queue.put(None)  # Sentinel

        asyncio.create_task(signal_end())

        # Stream events to the client as they arrive
        while True:
            item = await queue.get()
            if item is None:
                break
            event, payload = item
            data = json.dumps({"event": event, **payload})
            yield f"data: {data}\n\n"

        yield "data: {\"event\": \"stream_end\"}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
