"""
api/reports.py
--------------
Endpoint to retrieve the final investigation report.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.exceptions import ForbiddenException, NotFoundException
from app.dependencies.auth import get_current_user
from app.schemas.report import (
    ReportResponse,
    TimelineEntryResponse,
    ConnectionEntryResponse,
    PersonEntryResponse,
)
from app.services.investigation_service import get_investigation
from app.services.report_export_service import ExportFormat, export_report
from app.services.report_service import get_report

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/{investigation_id}", response_model=ReportResponse)
async def get_report_endpoint(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> ReportResponse:
    """
    Return the final report for a completed investigation.
    Raises 404 if the investigation is not yet complete.
    """
    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")

    report = await get_report(investigation_id)
    if not report:
        raise NotFoundException("Report not yet available for this investigation.")

    return ReportResponse(
        summary=report.get("summary", ""),
        findings=report.get("findings", []),
        timeline=[TimelineEntryResponse(**t) for t in report.get("timeline", [])],
        connections=[ConnectionEntryResponse(**c) for c in report.get("connections", [])],
        persons=[PersonEntryResponse(**p) for p in report.get("persons", [])],
        unresolved=report.get("unresolved", []),
    )


@router.get("/{investigation_id}/export/{file_format}")
async def export_report_endpoint(
    investigation_id: str,
    file_format: ExportFormat,
    current_user: dict = Depends(get_current_user),
) -> Response:
    """Download the already saved report as PDF, DOCX, or JSON."""
    inv = await get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")

    report = await get_report(investigation_id)
    if not report:
        raise NotFoundException("Report not yet available for this investigation.")

    content, media_type, filename = export_report(report, file_format)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
