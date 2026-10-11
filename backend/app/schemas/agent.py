"""
schemas/agent.py
----------------
Request and response schemas for agent run endpoints.
"""

from typing import Literal
from pydantic import BaseModel


class AgentLineResponse(BaseModel):
    """Matches frontend AgentLine type."""
    kind: Literal["info", "ok", "scan", "warn", "error"]
    text: str


class AgentRunResponse(BaseModel):
    """Matches frontend AgentRun type."""
    id: Literal["collector", "analyzer", "timeline", "linker", "validator", "subject", "report"]
    status: Literal["idle", "running", "done", "completed_with_warnings", "completed_with_fallback", "failed", "blocked"]
    lines: list[AgentLineResponse] = []
    summary: list[str] = []

    @classmethod
    def from_doc(cls, doc: dict) -> "AgentRunResponse":
        return cls(
            id=doc["agent_id"],
            status=doc.get("status", "idle"),
            lines=[AgentLineResponse(**line) for line in doc.get("lines", [])],
            summary=doc.get("summary", []),
        )
