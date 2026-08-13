"""
schemas/evidence.py
-------------------
Request and response schemas for evidence endpoints.
"""

from typing import Literal, Optional
from pydantic import BaseModel


class EvidenceItemResponse(BaseModel):
    """Matches the frontend EvidenceItem type exactly."""
    id: str         # evidence_id: E-001, E-002, …
    name: str       # original filename
    kind: Literal["doc", "image", "audio", "text", "chat", "map", "video"]
    size: Optional[int] = None
