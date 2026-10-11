"""
services/report_service.py
---------------------------
Store and retrieve the final investigation report in MongoDB (one per investigation).
"""

from typing import Optional

from app.database import collections


async def save_report(investigation_id: str, report: dict) -> None:
    """Insert or replace the investigation's report (the document _id is kept on re-runs)."""
    await collections.reports().replace_one(
        {"investigation_id": investigation_id},
        {"investigation_id": investigation_id, **report},
        upsert=True,
    )


async def update_report(investigation_id: str, **fields) -> None:
    """Set top-level fields on an existing report (e.g. the run diagnostics)."""
    await collections.reports().update_one({"investigation_id": investigation_id}, {"$set": fields})


async def get_report(investigation_id: str) -> Optional[dict]:
    return await collections.reports().find_one({"investigation_id": investigation_id})
