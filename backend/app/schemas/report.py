"""
schemas/report.py
-----------------
Request and response schemas for the final report endpoint.
"""

from pydantic import BaseModel


class TimelineEntryResponse(BaseModel):
    time: str
    event: str
    refs: list[str] = []


class ConnectionEntryResponse(BaseModel):
    a: str
    b: str
    reason: str


class PersonEntryResponse(BaseModel):
    name: str
    note: str


class ReportResponse(BaseModel):
    """Matches frontend FinalReport type exactly."""
    summary: str
    findings: list[str] = []
    timeline: list[TimelineEntryResponse] = []
    connections: list[ConnectionEntryResponse] = []
    persons: list[PersonEntryResponse] = []
    unresolved: list[str] = []
