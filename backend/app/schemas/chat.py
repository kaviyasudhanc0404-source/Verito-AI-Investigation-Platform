"""
schemas/chat.py
---------------
Request and response schemas for the RAG chat endpoint.
"""

from typing import Literal
from pydantic import BaseModel


class ChatRequest(BaseModel):
    text: str


class ChatMessageResponse(BaseModel):
    """Matches frontend ChatMessage type."""
    id: str
    role: Literal["user", "verito"]
    text: str
    refs: list[str] = []

    @classmethod
    def from_doc(cls, doc: dict) -> "ChatMessageResponse":
        return cls(id=doc["_id"], role=doc["role"], text=doc["text"], refs=doc.get("refs", []))
