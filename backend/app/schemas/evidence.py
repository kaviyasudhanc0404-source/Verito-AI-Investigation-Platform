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

    @classmethod
    def from_doc(cls, doc: dict) -> "EvidenceItemResponse":
        return cls(id=doc["evidence_id"], name=doc["filename"], kind=doc["kind"], size=doc.get("size_bytes"))
