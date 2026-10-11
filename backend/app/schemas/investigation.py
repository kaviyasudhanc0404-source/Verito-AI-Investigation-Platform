"""
schemas/investigation.py
-------------------------
Request and response schemas for investigation endpoints.
"""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel

from app.schemas.agent import AgentRunResponse
from app.schemas.report import ReportResponse
from app.schemas.chat import ChatMessageResponse
from app.schemas.evidence import EvidenceItemResponse


class CreateInvestigationRequest(BaseModel):
    title: str
    prompt: str


class UpdateInvestigationRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


class InvestigationResponse(BaseModel):
    """Full investigation returned to the frontend — matches CaseRecord shape."""
    id: str
    title: str
    prompt: str
    status: Literal["processing", "complete", "failed"]
    pinned: bool = False
    created_at: datetime
    evidence: list[EvidenceItemResponse] = []
    runs: list[AgentRunResponse] = []
    report: Optional[ReportResponse] = None
    chat: list[ChatMessageResponse] = []


class InvestigationListItem(BaseModel):
    """Lightweight item for the sidebar list."""
    id: str
    title: str
    status: Literal["processing", "complete", "failed"]
    pinned: bool = False
    created_at: datetime

    @classmethod
    def from_doc(cls, doc: dict) -> "InvestigationListItem":
        return cls(id=doc["_id"], title=doc["title"], status=doc["status"],
                   pinned=doc.get("pinned", False), created_at=doc["created_at"])
