"""
services/report_service.py
---------------------------
Store and retrieve the final investigation report in MongoDB.
"""

from typing import Optional
from app.database import collections
from app.utils.id_generator import new_report_id


async def save_report(investigation_id: str, report_data: dict) -> dict:
    """
    Upsert the final report for an investigation.
    Returns the report document.
    """
    doc = {
        "_id": new_report_id(),
        "investigation_id": investigation_id,
        **report_data,
    }
    await collections.reports().replace_one(
        {"investigation_id": investigation_id},
        doc,
        upsert=True,
    )
    return doc


async def get_report(investigation_id: str) -> Optional[dict]:
    """Fetch the report for an investigation."""
    return await collections.reports().find_one(
        {"investigation_id": investigation_id}
    )
