"""
api/reports.py
--------------
Read the final report, or download it as PDF / DOCX / JSON.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.exceptions import NotFoundException
from app.dependencies.auth import get_owned_investigation
from app.schemas.report import ReportResponse
from app.services.report_export_service import ExportFormat, export_report
from app.services.report_service import get_report

router = APIRouter(prefix="/reports", tags=["Reports"])


async def _saved_report(investigation_id: str) -> dict:
    report = await get_report(investigation_id)
    if not report:
        raise NotFoundException("Report not yet available for this investigation.")
    return report


@router.get("/{investigation_id}", response_model=ReportResponse)
async def get_report_endpoint(inv: dict = Depends(get_owned_investigation)) -> ReportResponse:
    return ReportResponse.from_document(await _saved_report(inv["_id"]))


@router.get("/{investigation_id}/export/{file_format}")
async def export_report_endpoint(file_format: ExportFormat, inv: dict = Depends(get_owned_investigation)) -> Response:
    report = ReportResponse.from_document(await _saved_report(inv["_id"]))
    content, media_type, filename = export_report(report, file_format, case_title=inv.get("title", ""))
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
