"""
services/investigation_service.py
-----------------------------------
CRUD operations for investigations in MongoDB.
"""

import shutil
from pathlib import Path
from typing import Optional

from pymongo import ReturnDocument

from app.core.config import get_settings
from app.database import collections
from app.utils.id_generator import new_investigation_id
from app.utils.datetime_utils import utcnow

settings = get_settings()


async def create_investigation(
    user_id: str,
    title: str,
    prompt: str,
) -> dict:
    """Insert a new investigation and return the document."""
    now = utcnow()
    doc = {
        "_id": new_investigation_id(),
        "user_id": user_id,
        "title": title,
        "prompt": prompt,
        "status": "processing",
        "pinned": False,
        "created_at": now,
        "updated_at": now,
    }
    await collections.investigations().insert_one(doc)
    return doc


async def get_investigation(investigation_id: str) -> Optional[dict]:
    """Fetch a single investigation by ID."""
    return await collections.investigations().find_one({"_id": investigation_id})


async def list_user_investigations(user_id: str) -> list[dict]:
    """Return all investigations for a user, newest first."""
    cursor = collections.investigations().find(
        {"user_id": user_id},
        sort=[("pinned", -1), ("created_at", -1)],
    )
    return await cursor.to_list(length=None)


async def update_investigation(
    investigation_id: str,
    *,
    title: str | None = None,
    pinned: bool | None = None,
) -> Optional[dict]:
    """Update editable investigation metadata and return the updated document."""
    updates: dict = {"updated_at": utcnow()}
    if title is not None:
        updates["title"] = title
    if pinned is not None:
        updates["pinned"] = pinned

    return await collections.investigations().find_one_and_update(
        {"_id": investigation_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )


async def update_investigation_status(
    investigation_id: str,
    status: str,
) -> None:
    """Update the status field of an investigation."""
    await collections.investigations().update_one(
        {"_id": investigation_id},
        {"$set": {"status": status, "updated_at": utcnow()}},
    )


def _delete_investigation_uploads(user_id: str, investigation_id: str, evidence_docs: list[dict]) -> None:
    """Remove saved upload files for one investigation, staying inside UPLOAD_DIR."""
    upload_root = Path(settings.upload_dir).resolve()
    investigation_dir = (upload_root / user_id / investigation_id).resolve()

    if upload_root in investigation_dir.parents and investigation_dir.exists():
        shutil.rmtree(investigation_dir)
        return

    for ev in evidence_docs:
        raw_path = ev.get("file_path")
        if not raw_path:
            continue
        path = Path(raw_path).resolve()
        if upload_root in path.parents and path.exists() and path.is_file():
            path.unlink()


async def delete_investigation(investigation_id: str, user_id: str) -> None:
    """
    Delete an investigation and all related data from MongoDB.
    Note: Qdrant chunks are deleted separately in the API route.
    """
    evidence_docs = await collections.evidence().find(
        {"investigation_id": investigation_id, "user_id": user_id}
    ).to_list(length=None)
    _delete_investigation_uploads(user_id, investigation_id, evidence_docs)

    await collections.investigations().delete_one({"_id": investigation_id, "user_id": user_id})
    await collections.evidence().delete_many({"investigation_id": investigation_id})
    await collections.agent_runs().delete_many({"investigation_id": investigation_id})
    await collections.reports().delete_one({"investigation_id": investigation_id})
    await collections.chat_messages().delete_many({"investigation_id": investigation_id})
