"""
models/agent.py
---------------
MongoDB document model for a single agent run within an investigation.
"""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


AgentId = Literal["collector", "analyzer", "timeline", "linker", "subject", "report"]
AgentStatus = Literal["idle", "running", "done", "failed"]
LineKind = Literal["info", "ok", "scan", "warn"]


class AgentLine(BaseModel):
    """A single activity log line emitted by an agent — matches frontend type."""
    kind: LineKind
    text: str


class AgentRunModel(BaseModel):
    """Represents an agent_runs document in MongoDB."""

    id: str = Field(alias="_id")
    investigation_id: str
    agent_id: AgentId
    status: AgentStatus = "idle"
    lines: list[AgentLine] = []
    summary: list[str] = []
    output: dict[str, Any] = {}    # Structured output from the agent
    updated_at: datetime

    model_config = {"populate_by_name": True}
