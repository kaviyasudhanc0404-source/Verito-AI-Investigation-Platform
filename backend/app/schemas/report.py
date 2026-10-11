"""
schemas/report.py
-----------------
Response schema for the final investigation report (matches the frontend
FinalReport type). `ReportResponse.from_document` also reads reports saved by
earlier versions (string findings, `persons`, `unresolved`, `data_quality`), so
old cases still open.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

_EVIDENCE_REF = re.compile(r"\bE-\d{3,}\b")


class GlanceItem(BaseModel):
    label: str
    value: str


class Excerpt(BaseModel):
    ref: str
    location: str = ""
    text: str


class Calculation(BaseModel):
    description: str = ""
    text: str = ""


class Finding(BaseModel):
    id: str = ""
    title: str
    detail: str = ""
    why: str = ""                       # plain-language "why this matters"
    severity: Literal["high", "medium", "low"] = "low"
    severity_basis: str = ""
    category: str = ""
    claim: str = ""                     # Established by the records | Documented observation | …
    confidence: str = ""
    confidence_basis: str = ""
    refs: list[str] = []
    records: list[str] = []             # exact record citations
    excerpts: list[Excerpt] = []
    calculation: Calculation | None = None
    qualifiers: list[str] = []          # context that may explain or limit the finding
    verify: list[str] = []              # how to verify it
    question: str = ""                  # the open question it raises
    source: str = ""                    # rule | document


class ExecutiveSummary(BaseModel):
    establishes: list[str] = []
    suggests: list[str] = []
    uncertain: list[str] = []
    verify_next: list[str] = []


class TimelineEvent(BaseModel):
    date: str
    time: str = ""
    zone: str = ""
    approximate: bool = False
    original: str = ""
    date_type: str = ""
    event: str
    category: str = ""
    refs: list[str] = []


class Person(BaseModel):
    name: str
    role: str = ""
    category: str = ""
    involvement: str = ""
    involvement_basis: str = ""
    note: str = ""
    context: list[str] = []
    identity_note: str = ""
    refs: list[str] = []


class LinkExcerpt(BaseModel):
    ref: str
    text: str


class Connection(BaseModel):
    a: str
    b: str
    a_name: str = ""
    b_name: str = ""
    type: str = ""
    method: str = ""
    reason: str
    strength: str = ""
    excerpts: list[LinkExcerpt] = []


class ReconciliationRow(BaseModel):
    check: str
    result: str
    refs: list[str] = []


class EvidenceEntry(BaseModel):
    id: str
    name: str
    description: str = ""
    status: str = ""
    records: int = 0
    chunks: int = 0
    notes: list[str] = []


class QualityStatus(BaseModel):
    status: str = "passed"              # passed | warning | partial | blocked
    policy: str = ""
    reasons: list[str] = []


class WithheldFinding(BaseModel):
    id: str
    title: str
    reasons: list[str] = []


class ReportResponse(BaseModel):
    title: str = ""
    status: str = "final"               # final | partial
    summary: str
    executive_summary: ExecutiveSummary = ExecutiveSummary()
    metadata: dict = {}
    at_a_glance: list[GlanceItem] = []
    scope: list[str] = []
    findings: list[Finding] = []
    reconciliation: list[ReconciliationRow] = []
    timeline: list[TimelineEvent] = []          # key events
    all_events: list[TimelineEvent] = []        # the full timeline
    people: list[Person] = []
    connections: list[Connection] = []
    open_questions: list[str] = []
    next_steps: list[str] = []
    evidence: list[EvidenceEntry] = []
    limitations: list[str] = []
    methodology: list[str] = []
    quality: QualityStatus = QualityStatus()
    withheld: list[WithheldFinding] = []
    diagnostics: dict = {}
    generated_by: str = ""
    generated_at: str = ""

    @classmethod
    def from_document(cls, doc: dict) -> "ReportResponse":
        if "people" in doc or "at_a_glance" in doc:
            doc = dict(doc)
            doc.setdefault("limitations", doc.get("data_quality", []))
            return cls.model_validate(doc)
        return cls(  # legacy report shape
            summary=doc.get("summary", ""),
            findings=[
                Finding(title=f, refs=_EVIDENCE_REF.findall(f)) if isinstance(f, str) else Finding.model_validate(f)
                for f in doc.get("findings", [])
            ],
            timeline=[TimelineEvent(date=t.get("time", ""), event=t.get("event", ""), refs=t.get("refs", []))
                      for t in doc.get("timeline", [])],
            people=[Person(name=p.get("name", ""), note=p.get("note", "")) for p in doc.get("persons", [])],
            connections=[Connection.model_validate(c) for c in doc.get("connections", [])],
            open_questions=doc.get("unresolved", []),
        )
