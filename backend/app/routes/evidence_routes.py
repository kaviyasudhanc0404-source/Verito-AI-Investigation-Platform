"""
routes/evidence_routes.py
--------------------------
Clean evidence upload and retrieval routes for the new architecture.

Endpoints:
  POST /evidence/upload/{investigation_id}  — upload + full pipeline
  GET  /evidence/{investigation_id}         — list evidence items
  DELETE /evidence/{investigation_id}       — remove evidence + Qdrant vectors

These routes are registered under /api prefix in main.py alongside
the existing api/evidence.py routes for backward compatibility.
New routes are mounted at /api (same prefix, FastAPI merges by path).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status

from app.core.config import get_settings
from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.dependencies.auth import get_current_user
from app.schemas.evidence import EvidenceItemResponse
from app.services.evidence_service import (
    process_and_store_evidence,
    get_evidence_for_investigation,
)
from app.services.investigation_service import get_investigation

settings = get_settings()
router = APIRouter(prefix="/evidence", tags=["Evidence"])

# Supported file extensions
ALLOWED_EXTENSIONS = {
    # Documents
    ".pdf", ".docx", ".doc", ".txt", ".csv",
    # Images
    ".jpg", ".jpeg", ".png", ".webp",
    # Email
    ".eml",
    # Chat (JSON exports accepted as chat logs)
    ".json",
    # Audio / Video (stored but not parsed by evidence collector)
    ".wav", ".mp3", ".m4a",
    ".mp4", ".mov",
}


def _validate_extension(filename: str) -> None:
    """Raise BadRequestException for unsupported file types."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise BadRequestException(
            f"File type '{ext}' is not supported. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )


@router.post(
    "/upload/{investigation_id}",
    response_model=list[EvidenceItemResponse],
    status_code=201,
    summary="Upload evidence files",
    description=(
        "Upload one or more evidence files for an investigation. "
        "Each file is saved to disk, then parsed, embedded, and stored in Qdrant Cloud. "
        "Supported: PDF (digital + scanned), JPEG/PNG/WEBP images, .eml emails, "
        ".txt/.json chat logs, CSV, DOCX."
    ),
)
async def upload_evidence(
    investigation_id: str,
    files: List[UploadFile] = File(...),
    current_user: dict = Depends(get_current_user),
) -> list[EvidenceItemResponse]:
    """Upload and ingest one or more evidence files."""
    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    results: list[EvidenceItemResponse] = []

    for upload in files:
        filename = upload.filename or "unnamed_file"
        _validate_extension(filename)

        file_bytes = await upload.read()
        if len(file_bytes) > max_bytes:
            raise BadRequestException(
                f"File '{filename}' exceeds the {settings.max_upload_mb} MB limit."
            )

        ev_doc = await process_and_store_evidence(
            file_bytes=file_bytes,
            filename=filename,
            user_id=current_user["_id"],
            investigation_id=investigation_id,
        )

        results.append(
            EvidenceItemResponse(
                id=ev_doc["evidence_id"],
                name=ev_doc["filename"],
                kind=ev_doc["kind"],
                size=ev_doc.get("size_bytes"),
            )
        )

    return results


@router.get(
    "/{investigation_id}",
    response_model=list[EvidenceItemResponse],
    summary="List evidence items",
)
async def list_evidence(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> list[EvidenceItemResponse]:
    """Return all evidence items for an investigation."""
    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")

    evidence_docs = await get_evidence_for_investigation(investigation_id)
    return [
        EvidenceItemResponse(
            id=ev["evidence_id"],
            name=ev["filename"],
            kind=ev["kind"],
            size=ev.get("size_bytes"),
        )
        for ev in evidence_docs
    ]


@router.delete(
    "/{investigation_id}",
    status_code=200,
    summary="Delete all evidence for an investigation",
)
async def delete_evidence(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Remove all evidence docs from MongoDB and Qdrant for an investigation."""
    from app.database import collections
    from app.services.vector_store import delete_investigation_chunks
    import asyncio

    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")

    # Delete from MongoDB
    await collections.evidence().delete_many(
        {"investigation_id": investigation_id, "user_id": current_user["_id"]}
    )

    # Delete from Qdrant (run in thread — sync client)
    await asyncio.to_thread(delete_investigation_chunks, investigation_id, current_user["_id"])

    return {"deleted": True, "investigation_id": investigation_id}
