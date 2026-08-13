"""
schemas/agent.py
----------------
Request and response schemas for agent run endpoints.
"""

from typing import Literal
from pydantic import BaseModel


class AgentLineResponse(BaseModel):
    """Matches frontend AgentLine type."""
    kind: Literal["info", "ok", "scan", "warn"]
    text: str


class AgentRunResponse(BaseModel):
    """Matches frontend AgentRun type."""
    id: Literal["collector", "analyzer", "timeline", "linker", "subject", "report"]
    status: Literal["idle", "running", "done", "failed"]
    lines: list[AgentLineResponse] = []
    summary: list[str] = []
