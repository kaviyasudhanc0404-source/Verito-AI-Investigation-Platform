"""
schemas/chat.py
---------------
Request and response schemas for the RAG chat endpoint.
"""

from datetime import datetime
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
