"""
api/agents.py
--------------
Endpoints to retrieve agent run state for an investigation.
Used by the frontend to restore progress after page refresh.
"""

from fastapi import APIRouter, Depends

from app.core.exceptions import ForbiddenException, NotFoundException
from app.database import collections
from app.dependencies.auth import get_current_user
from app.schemas.agent import AgentLineResponse, AgentRunResponse
from app.services.investigation_service import get_investigation

router = APIRouter(prefix="/agents", tags=["Agents"])

# The canonical pipeline order
AGENT_ORDER = ["collector", "analyzer", "timeline", "linker", "subject", "report"]


@router.get("/{investigation_id}", response_model=list[AgentRunResponse])
async def get_agent_runs(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> list[AgentRunResponse]:
    """
    Return all agent run states for an investigation.
    Used to restore live agent progress UI after page refresh.
    """
    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")

    run_docs = await collections.agent_runs().find(
        {"investigation_id": investigation_id}
    ).to_list(length=None)

    # Map by agent_id for ordered response
    run_map = {r["agent_id"]: r for r in run_docs}

    result = []
    for agent_id in AGENT_ORDER:
        if agent_id in run_map:
            run = run_map[agent_id]
            result.append(
                AgentRunResponse(
                    id=run["agent_id"],
                    status=run["status"],
                    lines=[AgentLineResponse(**l) for l in run.get("lines", [])],
                    summary=run.get("summary", []),
                )
            )
        else:
            # Agent hasn't started yet — return idle placeholder
            result.append(
                AgentRunResponse(
                    id=agent_id,  # type: ignore[arg-type]
                    status="idle",
                    lines=[],
                    summary=[],
                )
            )

    return result
