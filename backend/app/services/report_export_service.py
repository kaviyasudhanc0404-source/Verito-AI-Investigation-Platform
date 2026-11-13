"""
services/report_export_service.py
---------------------------------
Download the saved report as PDF, Word (DOCX) or JSON — no LLM call involved.

The report is first turned into format-neutral blocks (heading, subheading,
paragraph, bullets, table); the PDF and DOCX renderers only know how to draw those
blocks, so both formats always contain the same sections in the same order, with
the same footer and page numbers. Sections with no content are left out.
"""

from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from typing import Literal
from xml.sax.saxutils import escape

from app.core.exceptions import ServiceException
from app.schemas.report import ReportResponse

ExportFormat = Literal["pdf", "docx", "json"]

_CRIMSON = (185, 28, 53)
_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_SAFE = {"₹": "Rs. ", "↔": "<->", "→": "->", "✓": "-", "\u2010": "-", "\u2011": "-", "\u2212": "-",
             "\u00a0": " ", "\u202f": " "}
_FOOTER = "VERITO investigation report — confidential"

# ("heading", text) | ("subheading", text) | ("paragraph", markup) | ("bullets", [text]) | ("table", headers, rows)
# Paragraph markup is escaped text with optional <b>/<i> tags.
Block = tuple


def export_report(report: ReportResponse, file_format: ExportFormat, case_title: str = "") -> tuple[bytes, str, str]:
    """Return (file bytes, media type, filename)."""
    title = report.title or case_title or "Investigation report"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "report"
    if file_format == "json":
        return report.model_dump_json(indent=2).encode("utf-8"), "application/json", f"verito-{slug}.json"
    blocks = _blocks(report)
    if file_format == "pdf":
        return _render_pdf(title, blocks), "application/pdf", f"verito-{slug}.pdf"
    if file_format == "docx":
        return _render_docx(title, blocks), _DOCX_MEDIA, f"verito-{slug}.docx"
    raise ServiceException(f"Unsupported report export format: {file_format}")


def _blocks(report: ReportResponse) -> list[Block]:
    """The report as an ordered list of blocks."""
    refs = lambda items: ", ".join(items)  # noqa: E731
    blocks: list[Block] = []
    if report.status == "partial":
        blocks.append(("paragraph", "<b>Partial report.</b> " + escape(" ".join(report.quality.reasons))))
    blocks += [("heading", "Summary"), ("paragraph", escape(report.summary))]

    summary = report.executive_summary
    parts = (("What the records establish", summary.establishes),
             ("What the records suggest (needs review)", summary.suggests),
             ("What remains uncertain", summary.uncertain), ("What to verify next", summary.verify_next))
    if any(items for _, items in parts):
        blocks.append(("heading", "What the evidence shows"))
        for title, items in parts:
            if items:
                blocks += [("subheading", title), ("bullets", items)]
    if report.at_a_glance:
        blocks += [("heading", "At a glance"), ("table", ["", ""], [[g.label, g.value] for g in report.at_a_glance])]
    if report.scope:
        blocks += [("heading", "Scope"), ("bullets", report.scope)]

    if report.findings:
        blocks.append(("heading", "Key findings"))
        for f in report.findings:
            label = " · ".join(filter(None, (f"{f.severity} priority", f.claim)))
            blocks.append(("paragraph", f"<b>{escape(f.id)} {escape(f.title)}</b> ({escape(label)})"))
            if f.detail:
                blocks.append(("paragraph", escape(f.detail) + (f" <i>[Sources: {refs(f.refs)}]</i>" if f.refs else "")))
            for name, text in (("Why this matters", f.why),
                               ("Calculation", f.calculation.text if f.calculation else ""),
                               ("Confidence", f"{f.confidence} — {f.confidence_basis}" if f.confidence else ""),
                               ("Context", " ".join(f.qualifiers)), ("How to verify", " ".join(f.verify))):
                if text:
                    blocks.append(("paragraph", f"<i>{name}:</i> {escape(text)}"))
    if report.reconciliation:
        blocks += [("heading", "Reconciliation results"),
                   ("table", ["Check", "Result", "Sources"], [[r.check, r.result, refs(r.refs)] for r in report.reconciliation])]
    if report.timeline:
        blocks += [("heading", "Key events"),
                   ("table", ["Date", "Event", "Sources"],
                    [[" ".join(filter(None, (e.date, e.time, e.zone))), e.event, refs(e.refs)] for e in report.timeline])]
    if report.people:
        blocks += [("heading", "People involved"),
                   ("table", ["Name", "Role", "Involvement", "What the evidence shows"],
                    [[p.name, p.role or p.category, " — ".join(filter(None, (p.involvement, p.involvement_basis))) or "—",
                      " ".join(filter(None, (p.note, *p.context, p.identity_note)))] for p in report.people])]
    if report.connections:
        blocks += [("heading", "How the evidence connects"),
                   ("table", ["Files", "Link", "Shared"],
                    [[f"{c.a} ↔ {c.b}", c.type or c.strength, c.reason] for c in report.connections])]
    for heading, items in (("Open questions", report.open_questions), ("Recommended next steps", report.next_steps)):
        if items:
            blocks += [("heading", heading), ("bullets", items)]
    if report.evidence:
        blocks += [("heading", "Evidence inventory"),
                   ("table", ["ID", "File", "Contents", "Processing"],
                    [[e.id, e.name, e.description,
                      " — ".join(filter(None, (e.status.replace("_", " "), " ".join(e.notes))))] for e in report.evidence])]
    for heading, items in (("Limitations", report.limitations), ("Methodology", report.methodology)):
        if items:
            blocks += [("heading", heading), ("bullets", items)]

    with_excerpts = [f for f in report.findings if f.excerpts]
    if with_excerpts:
        blocks.append(("heading", "Appendix A — Source excerpts"))
        for f in with_excerpts:
            blocks += [("subheading", f"{f.id} {f.title}"),
                       ("bullets", [f"{x.ref} · {x.location}: {x.text}" for x in f.excerpts])]
    if report.withheld:
        blocks += [("heading", "Appendix B — Findings withheld by validation"),
                   ("bullets", [f"{w.id} {w.title}: {'; '.join(w.reasons)}" for w in report.withheld])]
    stages = [d for d in report.diagnostics.values() if isinstance(d, dict)]
    if stages:
        blocks += [("heading", "Appendix C — Processing diagnostics"),
                   ("table", ["Step", "Status", "Time", "AI calls", "Fallbacks"],
                    [[d.get("name", ""), str(d.get("status", "")).replace("_", " "),
                      f"{d.get('duration_ms', 0) / 1000:.1f}s", str(d.get("llm", {}).get("calls", 0)),
                      str(d.get("fallbacks", 0))] for d in stages])]
    return blocks


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


# ── PDF ───────────────────────────────────────────────────────────────────────

def _render_pdf(title: str, blocks: list[Block]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import ListFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    def safe(text: str) -> str:
        """Text the PDF's built-in fonts (Windows-1252) can draw; anything else becomes its closest form."""
        for symbol, replacement in _PDF_SAFE.items():
            text = text.replace(symbol, replacement)
        text = unicodedata.normalize("NFKC", text)
        return text.encode("cp1252", "replace").decode("cp1252")

    styles = getSampleStyleSheet()
    crimson = colors.Color(*(c / 255 for c in _CRIMSON))
    heading = ParagraphStyle("H", parent=styles["Heading2"], textColor=crimson, spaceBefore=12, spaceAfter=6)
    subheading = ParagraphStyle("S", parent=styles["Heading4"], spaceBefore=6, spaceAfter=3)
    body = ParagraphStyle("B", parent=styles["BodyText"], fontSize=9.5, leading=13)
    cell = ParagraphStyle("C", parent=body, fontSize=8.5, leading=11)

    story = [Paragraph("VERITO · INVESTIGATION REPORT", ParagraphStyle("K", parent=body, textColor=crimson, fontName="Helvetica-Bold")),
             Paragraph(escape(safe(title)), styles["Title"]), Spacer(1, 4)]
    for block in blocks:
        kind = block[0]
        if kind == "heading":
            story.append(Paragraph(escape(block[1]), heading))
        elif kind == "subheading":
            story.append(Paragraph(escape(safe(block[1])), subheading))
        elif kind == "paragraph":
            story.append(Paragraph(safe(block[1]), body))
        elif kind == "bullets":
            story.append(ListFlowable([Paragraph(escape(safe(item)), body) for item in block[1]], bulletType="bullet"))
        elif kind == "table":
            headers, rows = block[1], block[2]
            data = ([[Paragraph(f"<b>{escape(h)}</b>", cell) for h in headers]] if any(headers) else []) + \
                   [[Paragraph(escape(safe(str(value))), cell) for value in row] for row in rows]
            table = Table(data, repeatRows=1 if any(headers) else 0, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke if any(headers) else colors.white),
            ]))
            story.append(table)

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(40, 24, _FOOTER)
        canvas.drawRightString(555, 24, f"Page {doc.page}")
        canvas.restoreState()

    buffer = BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                      bottomMargin=16 * mm, title=title).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


# ── DOCX ──────────────────────────────────────────────────────────────────────

def _render_docx(title: str, blocks: list[Block]) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    brand = doc.add_paragraph().add_run("VERITO · INVESTIGATION REPORT")
    brand.bold, brand.font.size, brand.font.color.rgb = True, Pt(9), RGBColor(*_CRIMSON)
    doc.add_heading(title, level=0)
    for block in blocks:
        kind = block[0]
        if kind == "heading":
            doc.add_heading(block[1], level=1)
        elif kind == "subheading":
            doc.add_heading(block[1], level=3)
        elif kind == "paragraph":
            _docx_rich_paragraph(doc, block[1])
        elif kind == "bullets":
            for item in block[1]:
                doc.add_paragraph(item, style="List Bullet")
        elif kind == "table":
            headers, rows = block[1], block[2]
            table = doc.add_table(rows=0, cols=len(headers))
            table.style = "Light Grid Accent 1"
            if any(headers):
                for cell, header in zip(table.add_row().cells, headers):
                    cell.text = header
            for row in rows:
                for cell, value in zip(table.add_row().cells, row):
                    cell.text = str(value)
    _docx_footer(doc)
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _docx_footer(doc) -> None:
    """Same footer as the PDF: the confidentiality note and the page number (a Word PAGE field)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    paragraph = doc.sections[0].footer.paragraphs[0]
    paragraph.add_run(f"{_FOOTER}    Page ").font.size = Pt(8)
    run = paragraph.add_run()
    run.font.size = Pt(8)
    for tag, value in (("w:fldChar", "begin"), ("w:instrText", "PAGE"), ("w:fldChar", "end")):
        element = OxmlElement(tag)
        if tag == "w:fldChar":
            element.set(qn("w:fldCharType"), value)
        else:
            element.set(qn("xml:space"), "preserve")
            element.text = value
        run._r.append(element)


def _docx_rich_paragraph(doc, markup: str) -> None:
    """Render the small <b>/<i> subset used in paragraph blocks."""
    paragraph = doc.add_paragraph()
    for token in re.split(r"(<b>.*?</b>|<i>.*?</i>)", markup):
        if not token:
            continue
        run = paragraph.add_run(_unescape(_plain(token)))
        run.bold, run.italic = token.startswith("<b>"), token.startswith("<i>")


def _unescape(text: str) -> str:
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
