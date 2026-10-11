"""
services/case_model.py
----------------------
The in-memory model of one investigation, shared by the downstream agents.

    case = Case.build(evidence_docs, prose_people={"E-003": ["A. Person"]})

It holds:
  • evidence   — every indexed item (id, filename, cleaned text, ingestion report)
  • records    — structured rows from every item (see services/records.py)
  • registry   — people, organisations and accounts with all their aliases
  • documents  — records merged by their own ID across sources, so the same order
                 in a PDF and a spreadsheet is one document with two citations
  • copies     — pairs of files that hold the same records (exports / duplicates);
                 they corroborate nothing on their own, so `independent_sources()`
                 counts them once
  • offset     — the dominant stated UTC offset, assumed for timestamps that state none
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.identities import IdentityRegistry
from app.services.records import Record, extract_records
from app.services.values import detect_currency, dominant_offset, format_amount, parse_amount, parse_timestamp

# Document roles → compacted field names, in priority order.
ROLES: dict[str, tuple[str, ...]] = {
    "date": ("date", "podate", "invoicedate", "transactiondate", "timestamp", "eventdate"),
    "paid_date": ("paiddate", "paidon", "paymentdate"),
    "approval_date": ("approvaldate",),
    "amount": ("totalamount", "total", "amount", "invoiceamount", "totalpovalue", "value"),
    "vendor": ("vendorid", "destinationvendorid", "vendor", "vendorname", "destinationname"),
    "po": ("ponumber", "pono", "po", "purchaseorder"),
    "invoice": ("invoicereference", "invoicenumber", "invoiceno", "invoice"),
    "requested_by": ("requestedby",),
    "approved_by": ("approvedby",),
    "description": ("description", "item", "details"),
}

_COPY_MIN_SHARED = 3          # documents two files must share to be treated as copies
_COPY_MIN_COVERAGE = 0.8      # … as a share of the smaller file's documents


@dataclass
class EvidenceItem:
    evidence_id: str
    filename: str
    text: str
    ingestion: dict = field(default_factory=dict)


@dataclass
class Document:
    """Records sharing one business identifier, merged across sources."""
    doc_id: str
    records: list[Record] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return next((r.kind for r in self.records if r.kind != "generic"), "generic")

    @property
    def refs(self) -> list[str]:
        return sorted({r.evidence_id for r in self.records})

    def value(self, role: str) -> str:
        """First value for a document role; "vendor" falls back to any counterparty-like field (Payee …)."""
        keys = ROLES.get(role, (role,))
        found = next((r.value(*keys) for r in self.records if r.value(*keys)), "")
        if not found and role == "vendor":
            found = next((r.role("counterparty") for r in self.records if r.role("counterparty")), "")
        return found

    def date(self, role: str = "date") -> datetime | None:
        stamp = parse_timestamp(self.value(role))
        return stamp.local if stamp else None

    def amount(self) -> float | None:
        return parse_amount(self.value("amount"))


@dataclass
class Case:
    evidence: list[EvidenceItem]
    records: list[Record]
    registry: IdentityRegistry
    documents: dict[str, Document]
    currency: str = "₹"
    offset: timedelta | None = None
    copies: list[tuple[str, str, int]] = field(default_factory=list)

    @classmethod
    def build(cls, evidence_docs: list[dict], prose_people: dict[str, list[str]] | None = None) -> "Case":
        evidence = [
            EvidenceItem(str(d["evidence_id"]), str(d.get("filename", "")), str(d.get("content") or ""),
                         dict(d.get("ingestion") or {}))
            for d in evidence_docs if d.get("evidence_id") and d.get("content")
        ]
        records = [r for item in evidence for r in extract_records(item.evidence_id, item.filename, item.text)]
        registry = IdentityRegistry()
        registry.add_records(records)
        for names in (prose_people or {}).values():
            for name in names:
                registry.add_person_mention(name)

        documents: dict[str, Document] = {}
        for record in records:
            if record.primary_id:
                documents.setdefault(record.primary_id, Document(record.primary_id)).records.append(record)

        case = cls(evidence, records, registry, documents,
                   currency=detect_currency(" ".join(item.text for item in evidence)),
                   offset=dominant_offset([r.when for r in records if r.when]))
        case.copies = case._find_copies()
        return case

    # ── Convenience lookups ───────────────────────────────────────────────────

    @property
    def evidence_ids(self) -> list[str]:
        return [item.evidence_id for item in self.evidence]

    def filename(self, evidence_id: str) -> str:
        return next((i.filename for i in self.evidence if i.evidence_id == evidence_id), evidence_id)

    def text_of(self, evidence_id: str) -> str:
        return next((i.text for i in self.evidence if i.evidence_id == evidence_id), "")

    def of_kind(self, kind: str) -> list[Document]:
        return [d for d in self.documents.values() if d.kind == kind]

    def is_party(self, doc_id: str) -> bool:
        """True for the ID of a person or organisation (master data), not a transaction document."""
        entity = self.registry.resolve(doc_id, record_uncertain=False)
        return bool(entity and doc_id in entity.ids)

    def records_of_kind(self, kind: str) -> list[Record]:
        return [r for r in self.records if r.kind == kind]

    def name(self, value: str) -> str:
        return self.registry.display(value)

    def money(self, amount: float | None) -> str:
        return format_amount(amount, self.currency) if amount is not None else "an unrecorded amount"

    def sort_key(self, record: Record) -> datetime:
        return record.when.sort_key(self.offset) if record.when else datetime.max

    def mentions_by_entity(self) -> dict[str, set[str]]:
        """Entity key → evidence IDs that mention it."""
        found: dict[str, set[str]] = {}
        for item in self.evidence:
            for key in self.registry.mentions(item.text):
                found.setdefault(key, set()).add(item.evidence_id)
        return found

    # ── Copies and independent sources ────────────────────────────────────────

    def _find_copies(self) -> list[tuple[str, str, int]]:
        """Pairs of files whose records are (almost) the same documents with the same values."""
        by_file: dict[str, dict[str, Record]] = {}
        for record in self.records:
            if record.primary_id:
                by_file.setdefault(record.evidence_id, {}).setdefault(record.primary_id, record)
        copies = []
        for a, b in itertools.combinations(sorted(by_file), 2):
            shared = by_file[a].keys() & by_file[b].keys()
            smaller = min(len(by_file[a]), len(by_file[b]))
            if len(shared) < _COPY_MIN_SHARED or len(shared) < _COPY_MIN_COVERAGE * smaller:
                continue
            agreeing = sum(_same_values(by_file[a][doc_id], by_file[b][doc_id]) for doc_id in shared)
            if agreeing >= 0.9 * len(shared):
                copies.append((a, b, len(shared)))
        return copies

    def independent_sources(self, refs: list[str] | set[str]) -> int:
        """How many of `refs` are independent, counting each group of copies once."""
        parent = {ref: ref for ref in refs}

        def root(ref: str) -> str:
            while parent[ref] != ref:
                ref = parent[ref]
            return ref

        for a, b, _ in self.copies:
            if a in parent and b in parent:
                parent[root(a)] = root(b)
        return len({root(ref) for ref in parent})

    def is_copy_pair(self, a: str, b: str) -> bool:
        return any({a, b} == {x, y} for x, y, _ in self.copies)


def _same_values(a: Record, b: Record) -> bool:
    """True when every field the two records share holds the same normalised value."""
    left = {k: _normal(v) for k, v in a.compact_fields.items()}
    right = {k: _normal(v) for k, v in b.compact_fields.items()}
    shared = left.keys() & right.keys()
    return bool(shared) and all(left[k] == right[k] for k in shared)


def _normal(value: str) -> str:
    amount = parse_amount(value)
    if amount is not None:
        return f"{amount:.2f}"
    stamp = parse_timestamp(value)
    if stamp:
        return stamp.local.isoformat()
    return " ".join(value.lower().split())
