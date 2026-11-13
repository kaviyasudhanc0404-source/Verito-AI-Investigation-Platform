"""
Golden regression test: the Meridian procurement case end to end, without network services.

Runs the real Timeline Builder, Evidence Linker, Finding Validator, Subject
Analyzer and Report Generator on the fixture case (LLM disabled) and checks that
the red flags, timeline, people and report are correct, cited and readable.
"""

import pytest

from app.agents import evidence_analyzer
from app.schemas.report import ReportResponse
from app.services.reconciliation import find_observations
from app.services.report_export_service import export_report
from app.services.values import find_ids, format_amount, parse_amount, parse_timestamp
from tests.conftest import run_downstream


@pytest.fixture(scope="module")
def outputs(meridian_case):
    return run_downstream(meridian_case)


@pytest.fixture(scope="module")
def report(outputs) -> ReportResponse:
    return ReportResponse.from_document(outputs["saved_report"])


# ── Values and records ────────────────────────────────────────────────────────

def test_value_parsers():
    assert find_ids("POs PO-2026-0342, 0349 and 0355 for MIL-VEN-3309 (E-001)") == [
        "PO-2026-0342", "MIL-VEN-3309", "PO-2026-0349", "PO-2026-0355"]
    assert find_ids("EVIDENCE EMPLOYEE VENDOR INVOICE") == []          # plain words are not IDs
    assert parse_amount("₹2,34,230") == 234230 and format_amount(1373520) == "₹13,73,520"
    assert parse_timestamp("07-Mar-26").local.isoformat() == "2026-03-07T00:00:00"
    stamp = parse_timestamp("2026-03-07T14:26:08+05:30")
    assert stamp.has_time and stamp.zone == "UTC+05:30" and stamp.sort_key().isoformat() == "2026-03-07T08:56:08"


def test_records_are_typed(meridian_case):
    kinds = {kind: len(meridian_case.records_of_kind(kind)) for kind in (
        "purchase_order", "invoice", "payment", "call", "audit_event", "message", "email", "vendor_event")}
    assert kinds == {"purchase_order": 9, "invoice": 8, "payment": 7, "call": 8, "audit_event": 13,
                     "message": 4, "email": 3, "vendor_event": 4}
    po = meridian_case.documents["PO-2026-0342"]
    assert po.refs == ["E-008", "E-009"]                               # same PO in PDF table and spreadsheet
    assert meridian_case.offset is not None                            # the chat/audit exports state +05:30


def test_identity_registry_resolves_every_alias(meridian_case):
    name = meridian_case.name
    for alias in ("MIL-EMP-0587", "A. Kumaras wamy (0587)", "Mr. Kumaraswamy", "arvind.kumaraswamy@meridian.example"):
        assert name(alias) == "Arvind Kumaraswamy"
    for alias in ("MIL-VEN-3309", "Vendor Contact - KBS", "Kovai Building Sol.", "accounts@kovai-build.example"):
        assert name(alias) == "Kovai Building Solutions"
    assert name("R. Iyer (0203)") == "Ramesh Iyer"
    assert "Vendor Contact - KBS" in meridian_case.registry.uncertain_aliases["MIL-VEN-3309"]   # initials only


# ── Red flags ─────────────────────────────────────────────────────────────────

def test_red_flags(meridian_case):
    flags = {o.category: o for o in find_observations(meridian_case)}
    assert set(flags) >= {"Approval controls", "Segregation of duties", "Vendor onboarding", "Payments",
                          "Record consistency", "Communications"}
    assert "₹9,86,900" in flags["Approval controls"].detail
    assert "5–8 minutes" in flags["Segregation of duties"].detail and "10.24.18.57" in flags["Segregation of duties"].detail
    onboarding = flags["Vendor onboarding"].detail
    assert "recorded by Arvind Kumaraswamy" in onboarding and "3 days before the first purchase order" in onboarding
    assert "All other vendor empanelments in the records were recorded by Ramesh Iyer" in onboarding

    details = " ".join(o.detail for o in find_observations(meridian_case))
    assert "releasing payment for INV-KBS-0119 on 09 Apr 2026" in details
    assert "call at 15:26, while PO-2026-0355 was created (15:20) and approved (15:27)" in details
    assert "recorded as paid on 10 Mar 2026, but bank payment TXN-2026-0318 is dated 15 Mar 2026" in details
    assert all(o.refs and o.records and o.verify for o in find_observations(meridian_case))


def test_core_checks_do_not_repeat_adapter_findings(meridian_case):
    observations = find_observations(meridian_case)
    payment_flags = [o for o in observations if any(r.primary_id == "AUD-2026-0409-1647" for r in o.records)]
    assert len(payment_flags) == 1 and payment_flags[0].rule == "procurement.payment_before_documents"


# ── Agents ────────────────────────────────────────────────────────────────────

def test_timeline_is_chronological_and_never_invents_times(outputs):
    events = outputs["timeline"]["events"]
    assert [e["when"] for e in events] == sorted(e["when"] for e in events)
    call = next(e for e in events if e["event"].startswith("Arvind Kumaraswamy called") and e["date"] == "21 Mar 2026")
    assert call["time"] == "15:26" and call["event"].endswith("Kovai Building Solutions (9 min).")
    po = next(e for e in events if "Purchase order PO-2026-0342" in e["event"])
    assert po["time"] == "" and po["refs"] == ["E-008", "E-009"]            # one event, both sources
    assert not any("2017" in e["date"] or "2019" in e["date"] for e in events)   # outside the case period
    created = next(e for e in events if e["event"].startswith("Arvind Kumaraswamy created PO-2026-0342"))
    assert created["zone"] == "UTC+05:30" and created["original"].startswith("2026-03-07T14:26")


def test_links_explain_what_files_share(outputs):
    links = outputs["linker"]["links"]
    bank = next(l for l in links if {l["a"], l["b"]} == {"E-002", "E-009"})
    assert "INV-KBS-0091" in bank["reason"] or "invoices" in bank["reason"]
    assert bank["type"] and bank["method"] and bank["excerpts"]
    assert len(outputs["linker"]["observations"]) >= 6


def test_validator_grades_every_finding(outputs):
    validator = outputs["validator"]
    assert validator["rejected"] == [] and validator["quality"]["status"] == "passed"
    by_id = {f["id"]: f for f in validator["findings"]}
    contact = next(f for f in by_id.values() if f["category"] == "Communications" and f["severity"] == "high")
    assert contact["confidence"] == "medium" and "initials" in contact["confidence_basis"]   # 'Vendor Contact - KBS'
    self_approval = next(f for f in by_id.values() if f["category"] == "Segregation of duties")
    assert self_approval["claim"] == "Established by the records" and self_approval["confidence"] == "high"
    threshold = next(f for f in by_id.values() if f["category"] == "Approval controls")
    assert threshold["claim"] == "Pattern requiring review"
    assert "₹9,86,900" in threshold["calculation"]["text"]


def test_people_profiles(outputs):
    people = {p["name"]: p for p in outputs["subject"]["persons"]}
    arvind = people["Arvind Kumaraswamy"]
    assert arvind["involvement"] == "High" and arvind["involvement_basis"].startswith("Requires further review")
    assert "requested 5 purchase orders" in arvind["note"].lower()
    assert people["Ramesh Iyer"]["involvement"] == "Low"                    # only mentioned for comparison
    assert people["Divya Raghunathan"]["category"] == "Investigation team"


def test_rule_based_report_is_complete_and_readable(report):
    assert report.title == "Review of dealings with Kovai Building Solutions" and report.status == "final"
    assert "do not establish wrongdoing" in report.summary
    assert [f.severity for f in report.findings] == sorted(
        (f.severity for f in report.findings), key=["high", "medium", "low"].index)
    assert all(f.refs and f.why and f.records and f.confidence for f in report.findings if f.source == "rule")
    glance = {g.label: g.value for g in report.at_a_glance}
    assert glance["Main party of concern"] == "Kovai Building Solutions"
    assert glance["Value of purchase orders"] == "₹9,86,900 across 5 purchase orders"
    assert report.open_questions and report.next_steps
    assert {e.id for e in report.evidence} == {"E-001", "E-002", "E-004", "E-005", "E-006", "E-007",
                                                 "E-008", "E-009", "E-010", "E-011"}
    assert report.limitations                                               # notes that AI writing was unavailable
    assert report.methodology and report.scope and report.reconciliation
    summary = report.executive_summary
    assert any("requested and approved" in item for item in summary.establishes)
    assert any("approval limit" in item for item in summary.suggests) and summary.verify_next


@pytest.mark.parametrize("file_format", ["pdf", "docx", "json"])
def test_report_exports(report, file_format):
    content, media_type, filename = export_report(report, file_format)
    assert content and filename.endswith(f".{file_format}") and media_type


def test_word_export_has_page_numbers(report):
    import io
    import zipfile

    content, _, _ = export_report(report, "docx")
    footer = "".join(zipfile.ZipFile(io.BytesIO(content)).read(n).decode() for n in zipfile.ZipFile(io.BytesIO(content)).namelist()
                     if n.startswith("word/footer"))
    assert "PAGE" in footer and "confidential" in footer


def test_legacy_reports_still_open():
    legacy = {"summary": "Old", "findings": ["Something [E-001]"], "persons": [{"name": "A", "note": "n"}],
              "timeline": [{"time": "1 Mar", "event": "x", "refs": ["E-001"]}], "connections": [], "unresolved": ["q"]}
    report = ReportResponse.from_document(legacy)
    assert report.findings[0].refs == ["E-001"] and report.people[0].name == "A" and report.open_questions == ["q"]
    previous = {"summary": "S", "people": [], "data_quality": ["note"], "findings": [
        {"title": "T", "detail": "D", "why": "W", "severity": "high", "refs": ["E-001"]}]}
    assert ReportResponse.from_document(previous).limitations == ["note"]


def test_analyzer_routes_structured_chunks_away_from_the_llm():
    assert evidence_analyzer._is_structured("[Row 1] A: 1 | B: 2\n[Row 2] A: 3 | B: 4")
    assert not evidence_analyzer._is_structured("I recall that Mr. Kumaraswamy asked for priority processing.")
    assert evidence_analyzer._factual_sentences(
        "Payment for INV-KBS-0119 was released on 09-Apr-2026 before the invoice date. Thanks.") == [
        "Payment for INV-KBS-0119 was released on 09-Apr-2026 before the invoice date."]


def test_document_findings_quote_their_source(report):
    document_findings = {f.title: f for f in report.findings if f.source == "document"}
    goods_receipt = next(f for title, f in document_findings.items() if "goods-receipt note was located" in title)
    assert goods_receipt.refs == ["E-001"] and goods_receipt.detail == "Stated in audit_findings.pdf."
    assert goods_receipt.claim == "Reported statement"
    assert any("processed on priority" in title for title in document_findings)       # witness account
    assert not any("do not establish" in f.title for f in report.findings)              # disclaimers are not findings


# ── Report review fixes ───────────────────────────────────────────────────────

def test_excerpts_show_every_kind_of_record_a_finding_rests_on(outputs):
    onboarding = next(f for f in outputs["validator"]["findings"] if f["category"] == "Vendor onboarding")
    texts = " ".join(e["text"] for e in onboarding["excerpts"])
    assert "Event Type" in texts or "Last Updated" in texts               # the vendor / bank-change records
    threshold = next(f for f in outputs["validator"]["findings"] if f["category"] == "Approval controls")
    orders = {i for e in threshold["excerpts"] for i in find_ids(e["text"]) if i.startswith("PO-")}
    assert len(orders) == 5                                               # all five orders, not one twice


def test_scope_counts_documents_not_copies(report):
    contents = next(item for item in report.scope if "evidence files were analysed" in item)
    assert "7 purchase orders" in contents                                # 9 rows, 7 distinct orders


def test_partial_identity_matches_never_give_high_confidence(report):
    for finding in report.findings:
        if "partial name" in finding.confidence_basis:
            assert finding.confidence != "high"


def test_pdf_text_survives_characters_the_font_cannot_draw(report):
    import pypdfium2 as pdfium

    from app.schemas.report import ReportResponse as Report

    content, _, _ = export_report(Report.model_validate({**report.model_dump(), "open_questions": [
        "What is the status of the goods‑receipt note?"]}), "pdf")
    pdf = pdfium.PdfDocument(content)
    text = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    assert "goods-receipt note" in text and "■" not in text
