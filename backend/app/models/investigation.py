"""
models/investigation.py
------------------------
MongoDB document model for an investigation (case).
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class InvestigationModel(BaseModel):
    """Represents an investigation document stored in MongoDB."""

    id: str = Field(alias="_id")
    user_id: str
    title: str
    prompt: str
    status: Literal["processing", "complete", "failed"] = "processing"
    pinned: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True}
