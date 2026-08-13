"""
models/user.py
--------------
MongoDB document model for a VERITO user.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class UserModel(BaseModel):
    """Represents a user document stored in MongoDB."""

    id: str = Field(alias="_id")
    email: EmailStr
    name: str
    password_hash: Optional[str] = None   # None for Google-only accounts
    google_uid: Optional[str] = None       # None for email/password accounts
    created_at: datetime

    model_config = {"populate_by_name": True}
