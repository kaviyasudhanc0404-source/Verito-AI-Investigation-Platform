"""
Universal behaviour across case types, plus negative tests.

Each fixture case comes from a different domain and uses its own vocabulary; the
same pipeline must produce a correct, cited, cautious report for all of them,
with no case-specific code.
"""

import pytest

from app.schemas.report import ReportResponse
from app.services.case_model import Case
from app.services.identities import IdentityRegistry
from app.services.reconciliation import find_observations
from app.services.reconciliation.base import Observation, calculation
from app.services.validation import validate_findings
from app.services.values import find_amounts, find_dates
from tests.conftest import load_docs, run_downstream


def _rules(case: Case) -> dict[str, Observation]:
    return {o.rule: o for o in find_observations(case)}


# ── Cyber / access incident ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def cyber(cyber_case):
    return run_downstream(cyber_case)


def test_access_logs_are_understood_without_a_schema(cyber_case):
    log = [r for r in cyber_case.records if r.filename == "vpn_auth_log.csv"]
    assert len(log) == 13 and all(r.kind == "audit_event" for r in log)
    assert log[0].roles["actor"] == "j.doe" and log[0].roles["ip"] == "198.51.100.20"
    account = cyber_case.registry.resolve("j.doe@corp.example")           # email local part → login name
    assert account and account.kind == "account" and account.name == "j.doe"


def test_access_red_flags_are_proportionate(cyber_case):
    rules = _rules(cyber_case)
    assert set(rules) == {"access.failed_then_success", "access.new_source", "access.activity_after_alert"}
    assert all(o.severity != "high" for o in rules.values())             # patterns, not conclusions
    assert "4 failed sign-ins on j.doe" in rules["access.failed_then_success"].title


def test_timeline_orders_mixed_timezones_in_utc(cyber):
    events = cyber["timeline"]["events"]
    order = [e["event"] for e in events]
    success = next(i for i, text in enumerate(order) if text.startswith("j.doe signed in to vpn-gw-01 from 203.0.113.7"))
    alert = next(i for i, text in enumerate(order) if text.startswith("ALERT-2026-0042"))
    download = next(i for i, text in enumerate(order) if text.startswith("j.doe downloaded"))
    assert success < alert < download                                     # 07:00 +05:30 is 01:30 UTC
    assert events[alert]["zone"] == "UTC+05:30" and events[download]["zone"] == "UTC"
    assert any(e["event"].startswith("j.doe failed to sign in") for e in events)


def test_cyber_report_names_the_account_cautiously(cyber):
    report = ReportResponse.from_document(cyber["saved_report"])
    assert report.title == "Review of activity on j.doe"
    person = next(p for p in report.people if p.name == "j.doe")
    assert person.category == "User account" and person.involvement == "Medium"
    links = cyber["linker"]["links"]
    assert any("203.0.113.7" in link["shared"]["identifier"] for link in links)   # IP links the files


# ── Conflicting and duplicate records ─────────────────────────────────────────

def test_core_checks_find_conflicts_duplicates_chronology_and_gaps(conflicts_case):
    rules = _rules(conflicts_case)
    conflict = rules["core.conflicting_values"]
    assert "INV-2026-0102" in conflict.detail and "INV-2026-0104" in conflict.detail and conflict.severity == "high"
    assert "INV-2026-0103 appears 2 times" in rules["core.duplicate_entries"].detail
    assert "TXN-2026-0903" in rules["core.chronology"].detail                     # paid before invoice date
    assert "INV-2026-0199" in rules["core.missing_counterparts"].detail
    assert "TXN-2026-0902 records $3,950 against INV-2026-0102" in rules["core.amount_mismatch"].detail


def test_conflicting_files_are_linked_as_conflicting(conflicts_case):
    links = run_downstream(conflicts_case)["linker"]["links"]
    pair = next(l for l in links if {l["a"], l["b"]} == {"E-001", "E-002"})
    assert pair["type"] == "conflicting records"


# ── HR / compliance ───────────────────────────────────────────────────────────

def test_hr_case_reports_statements_without_inventing_misconduct(hr_case):
    out = run_downstream(hr_case)
    report = ReportResponse.from_document(out["saved_report"])
    assert out["linker"]["observations"] == []                          # nothing to reconcile → no red flags
    assert "No cross-evidence red flags were detected" in report.summary
    assert all(p.involvement != "High" for p in report.people)
    assert all(f.claim == "Reported statement" for f in report.findings)
    approximate = [e for e in report.all_events if e.approximate]
    assert approximate and approximate[0].date == "~Jun 2026"          # "mid June 2026" keeps its uncertainty


# ── Negative tests ────────────────────────────────────────────────────────────

def test_copies_do_not_inflate_corroboration():
    ledger = load_docs("conflicts")[1]
    docs = [{**ledger, "evidence_id": "E-001", "filename": "ledger.csv"},
            {**ledger, "evidence_id": "E-002", "filename": "ledger_copy.csv"}]
    case = Case.build(docs)
    assert case.copies and case.independent_sources(["E-001", "E-002"]) == 1
    links = run_downstream(case)["linker"]["links"]
    assert links[0]["type"] == "duplicate copy" and links[0]["strength"] == "copy"


def test_shared_surname_does_not_merge_people():
    registry = IdentityRegistry()
    for name in ("Ravi Kumar", "Anita Kumar"):
        registry.add_person_mention(name)
    assert registry.resolve("Mr. Kumar") is None                         # ambiguous → not guessed
    assert registry.resolve("Ravi Kumar").name == "Ravi Kumar"


def test_records_without_dates_never_get_one():
    case = Case.build([{"evidence_id": "E-001", "filename": "people.csv",
                        "content": "[Row 1] Employee ID: EMP-1001 | Name: Ravi Kumar | Department: Finance"}])
    assert run_downstream(case)["timeline"]["events"] == []


def test_validator_withholds_unsupported_or_accusatory_findings(conflicts_case):
    record = conflicts_case.records[0]
    invented = Observation("Invoice INV-2026-0101 was paid twice", "It was paid $99,999 on 01 Jan 2026.",
                           "high", "Payments", records=[record], rule="test")
    accusatory = Observation("Vendor committed fraud", "The ledger lists INV-2026-0101.", "high", "Payments",
                             records=[record], rule="test")
    wrong_sum = Observation("Two invoices", "They total $4,200.00.", "low", "Payments", records=[record], rule="test",
                            calculation=calculation("sum", [4200.0, 3950.0], 4200.0, "Total", "amount"))
    accepted, rejected = validate_findings(conflicts_case, [invented, accusatory, wrong_sum], [])
    assert not accepted
    reasons = " ".join(" ".join(r["reasons"]) for r in rejected)
    assert "amounts not found" in reasons and "dates not found" in reasons
    assert "asserts guilt" in reasons and "does not recompute" in reasons


def test_inferences_are_never_labelled_as_established(meridian_case):
    accepted, _ = validate_findings(meridian_case, find_observations(meridian_case), [])
    assert all(f["claim"] != "Established by the records" for f in accepted if f["basis"] == "pattern")


def test_value_parsers_do_not_misread_words():
    assert find_amounts("INV-2026-0103 appears 2 times; Rs. 500 and $1,200.50") == [500.0, 1200.5]
    assert [m.timestamp.month_only for m in find_dates("in March 2026, then on 5 March 2026")] == [True, False]
    assert find_dates("around 5 March 2026")[0].timestamp.approximate


def test_master_data_attributes_are_not_documents():
    rows = "\n".join(f"[Row {n}] Vendor ID: VEN-{1000 + n} | Vendor Name: Vendor {n} Ltd | Registration No: REG-77-{n:04d} "
                     f"| Total Value: $5,000" for n in range(1, 4))
    payments = "\n".join(f"[Row {n}] Transaction ID: TXN-2026-{n:04d} | Transaction Date: 2026-03-0{n} | "
                         f"Vendor ID: VEN-{1000 + n} | Amount: $1,000" for n in range(1, 4))
    case = Case.build([{"evidence_id": "E-001", "filename": "vendors.csv", "content": rows},
                       {"evidence_id": "E-002", "filename": "payments.csv", "content": payments}])
    rules = _rules(case)
    assert "core.missing_counterparts" not in rules                  # REG-… numbers are attributes
    assert "core.amount_mismatch" not in rules                       # a payment is not compared with a vendor


def test_field_labels_are_not_quoted_as_statements(meridian_case):
    from app.services.document_facts import select_document_facts

    analyzer = {"statements": [{"text": "Transaction ID: TXN-2026-0420", "evidence_id": "E-002"},
                               {"text": "Submitted By: Ms. Divya Raghunathan, Senior Internal Auditor", "evidence_id": "E-001"}]}
    facts = select_document_facts(meridian_case, analyzer, [])
    assert not any(f["text"].startswith(("Transaction ID", "Submitted By")) for f in facts)


def test_statements_need_an_investigative_signal(meridian_case):
    from app.services.document_facts import select_document_facts

    analyzer = {"statements": [{"text": "PO-2026-0342 is ready for the Site B requirement, as discussed.", "evidence_id": "E-004"}]}
    assert not any("is ready for the Site B" in f["text"] for f in select_document_facts(meridian_case, analyzer, []))
