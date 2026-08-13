"""
models/report.py
----------------
MongoDB document model for the final investigation report.
"""

from pydantic import BaseModel, Field


class TimelineEntry(BaseModel):
    time: str
    event: str
    refs: list[str] = []


class ConnectionEntry(BaseModel):
    a: str   # Evidence ID
    b: str   # Evidence ID
    reason: str


class PersonEntry(BaseModel):
    name: str
    note: str


class ReportModel(BaseModel):
    """Represents a report document in MongoDB."""

    id: str = Field(alias="_id")
    investigation_id: str
    summary: str
    findings: list[str] = []
    timeline: list[TimelineEntry] = []
    connections: list[ConnectionEntry] = []
    persons: list[PersonEntry] = []
    unresolved: list[str] = []

    model_config = {"populate_by_name": True}
