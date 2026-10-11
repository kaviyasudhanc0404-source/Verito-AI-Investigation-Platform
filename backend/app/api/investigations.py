"""
api/investigations.py
----------------------
Investigation CRUD and the SSE stream of the agent pipeline.
"""

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agents.base import AGENT_IDS
from app.core.exceptions import ServiceException
from app.database import collections
from app.dependencies.auth import get_current_user, get_owned_investigation
from app.schemas.agent import AgentRunResponse
from app.schemas.chat import ChatMessageResponse
from app.schemas.evidence import EvidenceItemResponse
from app.schemas.investigation import (
    CreateInvestigationRequest,
    InvestigationListItem,
    InvestigationResponse,
    UpdateInvestigationRequest,
)
from app.schemas.report import ReportResponse
from app.services import investigation_service, vector_store
from app.services.evidence_service import get_evidence_for_investigation
from app.services.report_service import get_report
from app.workflows import pipeline

router = APIRouter(prefix="/investigations", tags=["Investigations"])

_HEARTBEAT_SECONDS = 15


async def _full_response(inv: dict) -> InvestigationResponse:
    runs = {
        run["agent_id"]: run
        for run in await collections.agent_runs().find({"investigation_id": inv["_id"]}).to_list(length=None)
    }
    report = await get_report(inv["_id"])
    chat = await collections.chat_messages().find(
        {"investigation_id": inv["_id"]}, sort=[("created_at", 1)]
    ).to_list(length=None)
    return InvestigationResponse(
        id=inv["_id"],
        title=inv["title"],
        prompt=inv["prompt"],
        status=inv["status"],
        pinned=inv.get("pinned", False),
        created_at=inv["created_at"],
        evidence=[EvidenceItemResponse.from_doc(e) for e in await get_evidence_for_investigation(inv["_id"])],
        runs=[AgentRunResponse.from_doc(runs[a]) for a in AGENT_IDS if a in runs],
        report=ReportResponse.from_document(report) if report else None,
        chat=[ChatMessageResponse.from_doc(m) for m in chat],
    )


@router.get("", response_model=list[InvestigationListItem])
async def list_investigations(current_user: dict = Depends(get_current_user)) -> list[InvestigationListItem]:
    docs = await investigation_service.list_user_investigations(current_user["_id"])
    return [InvestigationListItem.from_doc(inv) for inv in docs]


@router.post("", response_model=InvestigationResponse, status_code=201)
async def create_investigation(
    body: CreateInvestigationRequest,
    current_user: dict = Depends(get_current_user),
) -> InvestigationResponse:
    inv = await investigation_service.create_investigation(
        user_id=current_user["_id"],
        title=body.title.strip() or pipeline.UNTITLED,
        prompt=body.prompt,
    )
    return await _full_response(inv)


@router.get("/{investigation_id}", response_model=InvestigationResponse)
async def get_investigation(inv: dict = Depends(get_owned_investigation)) -> InvestigationResponse:
    return await _full_response(inv)


@router.patch("/{investigation_id}", response_model=InvestigationListItem)
async def update_investigation(
    body: UpdateInvestigationRequest,
    inv: dict = Depends(get_owned_investigation),
) -> InvestigationListItem:
    """Rename and/or pin an investigation."""
    title = (body.title.strip() or pipeline.UNTITLED) if body.title is not None else None
    updated = await investigation_service.update_investigation(inv["_id"], title=title, pinned=body.pinned)
    return InvestigationListItem.from_doc(updated)


@router.delete("/{investigation_id}", status_code=204)
async def delete_investigation(inv: dict = Depends(get_owned_investigation)) -> None:
    """Delete an investigation, its vectors in Qdrant and all related MongoDB data."""
    try:
        await asyncio.to_thread(vector_store.delete_investigation_chunks, inv["_id"], inv["user_id"])
    except Exception as exc:
        # Keep MongoDB metadata so the deletion can be retried without orphaning vectors.
        raise ServiceException(
            "Could not delete this investigation's Qdrant chunks. No records were removed; please retry."
        ) from exc
    await investigation_service.delete_investigation(inv["_id"], inv["user_id"])


@router.get("/{investigation_id}/stream")
async def stream_investigation(inv: dict = Depends(get_owned_investigation)) -> StreamingResponse:
    """
    SSE stream of the agent pipeline. Starts the pipeline if it is not already
    running; reconnecting clients attach to the run in progress. A completed
    investigation ends the stream immediately (the client then loads the report).
    """
    investigation_id = inv["_id"]
    finished = inv["status"] == "complete" and not pipeline.is_running(investigation_id)

    async def events() -> AsyncIterator[str]:
        if finished:
            yield _sse({"event": "stream_end"})
            return
        queue = pipeline.subscribe(investigation_id, inv["user_id"], inv.get("prompt", ""))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                event, payload = item
                yield _sse({"event": event, **payload})
            yield _sse({"event": "stream_end"})
        finally:
            pipeline.unsubscribe(investigation_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
