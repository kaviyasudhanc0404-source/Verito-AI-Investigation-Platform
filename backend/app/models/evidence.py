"""
models/evidence.py
------------------
MongoDB document model for a single uploaded evidence item.
"""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


EvidenceKind = Literal["doc", "image", "audio", "text", "chat", "map", "video"]


class EvidenceModel(BaseModel):
    """Represents an evidence document stored in MongoDB."""

    id: str = Field(alias="_id")
    investigation_id: str
    user_id: str
    evidence_id: str          # E-001, E-002, …
    filename: str
    kind: EvidenceKind
    file_path: str            # Relative path under uploads/
    size_bytes: int = 0
    content: Optional[str] = None     # Extracted text content
    created_at: datetime

    model_config = {"populate_by_name": True}
