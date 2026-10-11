"""
services/evidence_service.py
------------------------------
Store uploaded evidence files and their metadata.

Parsing, cleaning, chunking and embedding are owned by the Evidence Collector
agent so their progress can be streamed to the frontend.
"""

from pymongo import ReturnDocument

from app.database import collections
from app.utils.datetime_utils import utcnow
from app.utils.file_detector import detect_file_type, detect_kind
from app.utils.file_utils import save_upload
from app.utils.id_generator import format_evidence_id, new_evidence_db_id


async def _next_evidence_number(investigation_id: str) -> int:
    """Atomically allocate the next evidence number (E-001, E-002, …).

    Never reuses a number, even after deletions or concurrent uploads. Older
    investigations without a counter start after their highest existing ID.
    """
    existing = await collections.evidence().find(
        {"investigation_id": investigation_id}, {"evidence_id": 1}
    ).to_list(length=None)
    highest = max((int(doc["evidence_id"].split("-")[-1]) for doc in existing), default=0)
    inv = await collections.investigations().find_one_and_update(
        {"_id": investigation_id},
        [{"$set": {"evidence_seq": {"$add": [{"$max": [{"$ifNull": ["$evidence_seq", 0]}, highest]}, 1]}}}],
        return_document=ReturnDocument.AFTER,
    )
    return int(inv["evidence_seq"])


async def store_upload(*, file_bytes: bytes, filename: str, user_id: str, investigation_id: str) -> dict:
    """Save one uploaded file to disk and register it. Returns the evidence document."""
    doc = {
        "_id": new_evidence_db_id(),
        "investigation_id": investigation_id,
        "user_id": user_id,
        "evidence_id": format_evidence_id(await _next_evidence_number(investigation_id)),
        "filename": filename,
        "kind": detect_kind(filename),
        "file_type": detect_file_type(filename),
        "file_path": save_upload(file_bytes, filename, user_id, investigation_id),
        "size_bytes": len(file_bytes),
        "content": "",
        "created_at": utcnow(),
    }
    await collections.evidence().insert_one(doc)
    return doc


async def get_evidence_for_investigation(investigation_id: str) -> list[dict]:
    """All evidence documents of an investigation, oldest first."""
    return await collections.evidence().find(
        {"investigation_id": investigation_id}, sort=[("created_at", 1)]
    ).to_list(length=None)
