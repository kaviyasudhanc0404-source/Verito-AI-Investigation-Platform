"""
api/evidence.py
---------------
Evidence upload, listing and deletion.

Uploads are stored and registered quickly; parsing, chunking and embedding run
in the Evidence Collector so progress streams to the UI.
"""

import asyncio

from fastapi import APIRouter, Depends, File, UploadFile

from app.core.config import get_settings
from app.core.exceptions import BadRequestException
from app.database import collections
from app.dependencies.auth import get_owned_investigation
from app.schemas.evidence import EvidenceItemResponse
from app.services.evidence_service import get_evidence_for_investigation, store_upload
from app.services.vector_store import delete_investigation_chunks
from app.utils.file_detector import SUPPORTED_EXTENSIONS, is_supported
from app.utils.file_utils import safe_display_name

settings = get_settings()
router = APIRouter(prefix="/evidence", tags=["Evidence"])


@router.post("/upload/{investigation_id}", response_model=list[EvidenceItemResponse], status_code=201)
async def upload_evidence(
    files: list[UploadFile] = File(...),
    inv: dict = Depends(get_owned_investigation),
) -> list[EvidenceItemResponse]:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    stored = []
    for upload in files:
        filename = safe_display_name(upload.filename)
        if not is_supported(filename):
            raise BadRequestException(
                f"File type of '{filename}' is not supported. Allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
        file_bytes = await upload.read(max_bytes + 1)
        if len(file_bytes) > max_bytes:
            raise BadRequestException(f"File '{filename}' exceeds the {settings.max_upload_mb} MB limit.")
        doc = await store_upload(file_bytes=file_bytes, filename=filename, user_id=inv["user_id"], investigation_id=inv["_id"])
        stored.append(EvidenceItemResponse.from_doc(doc))
    return stored


@router.get("/{investigation_id}", response_model=list[EvidenceItemResponse])
async def list_evidence(inv: dict = Depends(get_owned_investigation)) -> list[EvidenceItemResponse]:
    return [EvidenceItemResponse.from_doc(doc) for doc in await get_evidence_for_investigation(inv["_id"])]


@router.delete("/{investigation_id}")
async def delete_evidence(inv: dict = Depends(get_owned_investigation)) -> dict:
    """Remove all evidence of an investigation from MongoDB and Qdrant Cloud."""
    await asyncio.to_thread(delete_investigation_chunks, inv["_id"], inv["user_id"])
    await collections.evidence().delete_many({"investigation_id": inv["_id"], "user_id": inv["user_id"]})
    return {"deleted": True, "investigation_id": inv["_id"]}
