"""
api/agents.py
-------------
Agent run states for an investigation (restores the live agent view after a refresh).
"""

from fastapi import APIRouter, Depends

from app.agents.base import AGENT_IDS
from app.database import collections
from app.dependencies.auth import get_owned_investigation
from app.schemas.agent import AgentRunResponse

router = APIRouter(prefix="/agents", tags=["Agents"])


@router.get("/{investigation_id}", response_model=list[AgentRunResponse])
async def get_agent_runs(inv: dict = Depends(get_owned_investigation)) -> list[AgentRunResponse]:
    runs = {
        run["agent_id"]: run
        for run in await collections.agent_runs().find({"investigation_id": inv["_id"]}).to_list(length=None)
    }
    return [AgentRunResponse.from_doc(runs.get(agent_id, {"agent_id": agent_id})) for agent_id in AGENT_IDS]
