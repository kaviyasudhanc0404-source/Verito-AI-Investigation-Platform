"""
api/evidence.py
---------------
Evidence upload and retrieval endpoints.
"""

from fastapi import APIRouter, Depends, UploadFile, File
from typing import List

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

# Allowed file extensions — must match all parsers in evidence_service._extract_content()
ALLOWED_EXTENSIONS = {
    # Documents (Docling)
    ".pdf", ".docx", ".doc", ".pptx", ".html", ".htm", ".md",
    # Data files
    ".csv", ".xlsx", ".xls", ".xml",
    # Email
    ".eml", ".msg",
    # Images
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif",
    # Chat / logs
    ".json", ".txt",
    # Archives
    ".zip",
    # Audio
    ".wav", ".mp3", ".m4a", ".ogg", ".flac",
    # Video
    ".mp4", ".avi", ".mov", ".mkv",
}



def _check_extension(filename: str) -> None:
    """Raise BadRequestException if the file type is not allowed."""
    from pathlib import Path
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
)
async def upload_evidence(
    investigation_id: str,
    files: List[UploadFile] = File(...),
    current_user: dict = Depends(get_current_user),
) -> list[EvidenceItemResponse]:
    """
    Upload one or more evidence files for an investigation.
    Files are saved and registered quickly; the Evidence Collector agent parses,
    chunks, embeds, and stores vectors during the investigation stream.
    """
    # Verify investigation exists and belongs to the user
    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    results = []

    for upload in files:
        filename = upload.filename or "unnamed_file"
        _check_extension(filename)

        file_bytes = await upload.read()
        if len(file_bytes) > max_bytes:
            raise BadRequestException(
                f"File '{filename}' exceeds the maximum size of {settings.max_upload_mb} MB."
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
                size=ev_doc["size_bytes"],
            )
        )

    return results


@router.get("/{investigation_id}", response_model=list[EvidenceItemResponse])
async def list_evidence(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> list[EvidenceItemResponse]:
    """List all evidence items for an investigation."""
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
