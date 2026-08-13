"""
models/chat.py
--------------
MongoDB document model for follow-up chat messages.
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class ChatMessageModel(BaseModel):
    """Represents a chat_messages document in MongoDB."""

    id: str = Field(alias="_id")
    investigation_id: str
    user_id: str
    role: Literal["user", "verito"]
    text: str
    refs: list[str] = []     # Evidence IDs cited in the answer
    created_at: datetime

    model_config = {"populate_by_name": True}
