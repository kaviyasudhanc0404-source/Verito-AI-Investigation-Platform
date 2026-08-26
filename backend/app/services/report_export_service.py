"""
services/report_export_service.py
---------------------------------
Render saved, validated investigation reports into downloadable files.

The report generator produces JSON once. This service formats that saved report
as PDF, DOCX, or JSON without calling the LLM again.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from typing import Any, Literal
from xml.sax.saxutils import escape

from app.core.exceptions import ServiceException

ExportFormat = Literal["pdf", "docx", "json"]

_GRAPHITE = "#16171d"
_CRIMSON = "#b91c35"
_GOLD = "#c9a84c"


def export_report(report: dict[str, Any], file_format: ExportFormat) -> tuple[bytes, str, str]:
    """Return report bytes, media type, and filename for the requested format."""
    investigation_id = str(report.get("investigation_id", "investigation"))
    if file_format == "json":
        return _export_json(report), "application/json", f"verito-report-{investigation_id}.json"
    if file_format == "pdf":
        return _export_pdf(report), "application/pdf", f"verito-report-{investigation_id}.pdf"
    if file_format == "docx":
        return (
            _export_docx(report),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            f"verito-report-{investigation_id}.docx",
        )
    raise ServiceException(f"Unsupported report export format: {file_format}")


def _export_json(report: dict[str, Any]) -> bytes:
    payload = report.get("validated_report") or _frontend_report_to_validated(report)
    data = {
        "investigation_id": report.get("investigation_id"),
        "report_version": report.get("report_version"),
        "generated_at": report.get("generated_at"),
        "generation_status": report.get("generation_status"),
        "validated_report": payload,
    }
    return json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")


def _export_pdf(report: dict[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise ServiceException("ReportLab is not installed. Run pip install reportlab.") from exc

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=f"Verito Report - {report.get('investigation_id', '')}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("VeritoTitle", parent=styles["Title"], textColor=colors.HexColor(_GRAPHITE)))
    styles.add(
        ParagraphStyle(
            "VeritoBrand",
            parent=styles["Normal"],
            alignment=TA_CENTER,
            textColor=colors.HexColor(_CRIMSON),
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
        )
    )
    styles.add(
        ParagraphStyle(
            "VeritoSection",
            parent=styles["Heading2"],
            textColor=colors.HexColor(_CRIMSON),
            spaceBefore=12,
            spaceAfter=6,
        )
    )
    styles.add(ParagraphStyle("VeritoSmall", parent=styles["Normal"], fontSize=8, leading=10))

    story = [
        Paragraph("VERITO", styles["VeritoBrand"]),
        Paragraph("Investigation Report", styles["VeritoTitle"]),
        Paragraph(_meta_line(report), styles["VeritoSmall"]),
        Spacer(1, 8),
    ]
    _add_report_sections(story, styles, report)
    doc.build(story, onFirstPage=_pdf_footer, onLaterPages=_pdf_footer)
    return buffer.getvalue()


def _export_docx(report: dict[str, Any]) -> bytes:
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt, RGBColor
    except ImportError as exc:
        raise ServiceException("python-docx is not installed. Run pip install python-docx.") from exc

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Pt(54)
    section.bottom_margin = Pt(54)
    section.left_margin = Pt(54)
    section.right_margin = Pt(54)

    brand = doc.add_paragraph()
    brand.alignment = WD_ALIGN_PARAGRAPH.CENTER
    brand_run = brand.add_run("VERITO")
    brand_run.bold = True
    brand_run.font.color.rgb = RGBColor(185, 28, 53)
    brand_run.font.size = Pt(11)

    title = doc.add_heading("Investigation Report", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(_meta_line(report))
    _add_docx_sections(doc, report)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _add_report_sections(story: list, styles: Any, report: dict[str, Any]) -> None:
    from reportlab.platypus import Paragraph, Spacer

    data = report.get("validated_report") or _frontend_report_to_validated(report)
    story.extend(
        [
            Paragraph("Executive Summary", styles["VeritoSection"]),
            Paragraph(_pdf_text(_summary(report, data)), styles["BodyText"]),
        ]
    )

    finding_rows = [["Finding", "Refs", "Confidence"]]
    for item in data.get("findings", []):
        finding_rows.append([
            item.get("text", ""),
            ", ".join(item.get("evidence_ids", [])),
            _percent(item.get("confidence")),
        ])
    _add_table(story, finding_rows, styles)

    timeline_rows = [["Time", "Event", "Refs"]]
    for item in data.get("timeline", []):
        timeline_rows.append([item.get("time", ""), item.get("event", ""), ", ".join(item.get("refs", []))])
    _add_table(story, timeline_rows, styles, heading="Timeline")

    connection_rows = [["Evidence A", "Evidence B", "Reason"]]
    for item in data.get("connections", []):
        connection_rows.append([item.get("a", ""), item.get("b", ""), item.get("reason", "")])
    _add_table(story, connection_rows, styles, heading="Evidence Connections")

    person_rows = [["Name", "Note", "Refs"]]
    for item in data.get("persons", []):
        person_rows.append([item.get("name", ""), item.get("note", ""), ", ".join(item.get("evidence_ids", []))])
    _add_table(story, person_rows, styles, heading="Persons / Subjects")

    story.append(Paragraph("Unresolved Issues", styles["VeritoSection"]))
    for item in data.get("unresolved", []):
        story.append(Paragraph(_pdf_text(f"- {item}"), styles["BodyText"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Evidence Reference Index", styles["VeritoSection"]))
    for ref in _evidence_index(data):
        story.append(Paragraph(_pdf_text(ref), styles["BodyText"]))


def _add_table(story: list, rows: list[list[str]], styles: Any, heading: str = "Key Findings") -> None:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    story.append(Paragraph(heading, styles["VeritoSection"]))
    if len(rows) == 1:
        story.append(Paragraph("No entries available.", styles["BodyText"]))
        return
    table_rows = [[Paragraph(_pdf_text(cell), styles["VeritoSmall"]) for cell in row] for row in rows]
    table = Table(table_rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_GRAPHITE)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(_GOLD)),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f4ef")]),
            ]
        )
    )
    story.extend([table, Spacer(1, 6)])


def _add_docx_sections(doc: Any, report: dict[str, Any]) -> None:
    data = report.get("validated_report") or _frontend_report_to_validated(report)
    doc.add_heading("Executive Summary", level=1)
    doc.add_paragraph(_summary(report, data))
    _docx_table(doc, "Key Findings", ["Finding", "Refs", "Confidence"], [
        [item.get("text", ""), ", ".join(item.get("evidence_ids", [])), _percent(item.get("confidence"))]
        for item in data.get("findings", [])
    ])
    _docx_table(doc, "Timeline", ["Time", "Event", "Refs"], [
        [item.get("time", ""), item.get("event", ""), ", ".join(item.get("refs", []))]
        for item in data.get("timeline", [])
    ])
    _docx_table(doc, "Evidence Connections", ["Evidence A", "Evidence B", "Reason"], [
        [item.get("a", ""), item.get("b", ""), item.get("reason", "")]
        for item in data.get("connections", [])
    ])
    _docx_table(doc, "Persons / Subjects", ["Name", "Note", "Refs"], [
        [item.get("name", ""), item.get("note", ""), ", ".join(item.get("evidence_ids", []))]
        for item in data.get("persons", [])
    ])
    doc.add_heading("Unresolved Issues", level=1)
    for item in data.get("unresolved", []):
        doc.add_paragraph(item, style="List Bullet")
    doc.add_heading("Evidence Reference Index", level=1)
    for ref in _evidence_index(data):
        doc.add_paragraph(ref, style="List Bullet")


def _docx_table(doc: Any, heading: str, headers: list[str], rows: list[list[str]]) -> None:
    doc.add_heading(heading, level=1)
    if not rows:
        doc.add_paragraph("No entries available.")
        return
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        table.rows[0].cells[index].text = header
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = str(value)


def _pdf_footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_GRAPHITE)
    canvas.drawString(40, 24, "VERITO Investigation Report")
    canvas.drawRightString(555, 24, f"Page {doc.page}")
    canvas.restoreState()


def _frontend_report_to_validated(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "executive_summary": report.get("summary", ""),
        "findings": [
            {"text": item, "evidence_ids": _refs(item), "confidence": None}
            for item in report.get("findings", [])
        ],
        "timeline": report.get("timeline", []),
        "connections": report.get("connections", []),
        "persons": [
            {
                "name": item.get("name", ""),
                "note": item.get("note", ""),
                "evidence_ids": _refs(item.get("note", "")),
            }
            for item in report.get("persons", [])
            if isinstance(item, dict)
        ],
        "unresolved": report.get("unresolved", []),
    }


def _summary(report: dict[str, Any], data: dict[str, Any]) -> str:
    return str(data.get("executive_summary") or report.get("summary") or "")


def _meta_line(report: dict[str, Any]) -> str:
    return (
        f"Investigation ID: {report.get('investigation_id', 'N/A')} | "
        f"Generated: {report.get('generated_at', 'N/A')} | "
        f"Version: {report.get('report_version', 'N/A')}"
    )


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "N/A"


def _refs(value: Any) -> list[str]:
    return sorted({ref.upper() for ref in re.findall(r"\bE-\d{3,}\b", str(value or ""), flags=re.IGNORECASE)})


def _pdf_text(value: Any) -> str:
    return escape(str(value or ""))


def _evidence_index(data: dict[str, Any]) -> list[str]:
    refs = set()
    for item in data.get("findings", []):
        refs.update(item.get("evidence_ids", []))
    for item in data.get("timeline", []):
        refs.update(item.get("refs", []))
    for item in data.get("connections", []):
        refs.update([item.get("a", ""), item.get("b", "")])
    for item in data.get("persons", []):
        refs.update(item.get("evidence_ids", []))
    return [ref for ref in sorted(refs) if ref]
