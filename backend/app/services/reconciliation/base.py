"""
services/reconciliation/base.py
-------------------------------
The Observation every reconciliation rule returns, and the wording helpers the
rules share.

An observation states what the records show — never intent or guilt — and
carries everything needed to check it: the exact records it rests on (with
excerpts), any calculation, context that may explain it, and how to verify it.

  basis       "documented"  the cited records state it directly
              "pattern"     an inference from several data points that needs explaining
  severity    high    a control appears bypassed, or a conflict directly affects money,
                      access or the order of approval
              medium  an irregularity or inconsistency with plausible routine explanations
              low     a weak or contextual signal worth noting
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.services.identities import Entity
from app.services.records import Record

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SEVERITY_CRITERIA = {
    "high": "A control appears to have been bypassed, or records conflict in a way that directly affects money, "
            "access or the order of approval.",
    "medium": "An irregularity or inconsistency that needs explaining but may have a routine cause.",
    "low": "A weak or contextual signal worth noting alongside other evidence.",
}
_MAX_EXCERPTS = 6


@dataclass
class Observation:
    title: str
    detail: str
    severity: str
    category: str
    records: list[Record] = field(default_factory=list)
    extra_refs: list[str] = field(default_factory=list)     # cited files that are not structured records
    subjects: list[str] = field(default_factory=list)       # entity keys the observation is about
    basis: str = "documented"
    rule: str = ""
    why: str = ""
    question: str = ""
    verify: list[str] = field(default_factory=list)
    qualifiers: list[str] = field(default_factory=list)     # context that may explain or limit it
    calculation: dict | None = None                          # see calculation()
    id: str = ""

    @property
    def refs(self) -> list[str]:
        return sorted({r.evidence_id for r in self.records} | set(self.extra_refs))

    @property
    def record_keys(self) -> set[str]:
        return {r.ref for r in self.records}

    def to_dict(self) -> dict:
        unique = list({r.ref: r for r in self.records}.values())
        return {
            "id": self.id, "title": self.title, "detail": self.detail, "severity": self.severity,
            "category": self.category, "refs": self.refs, "subjects": self.subjects, "basis": self.basis,
            "rule": self.rule, "why": self.why, "question": self.question, "verify": self.verify,
            "qualifiers": self.qualifiers, "calculation": self.calculation,
            "records": [r.citation for r in unique],
            "excerpts": [r.excerpt() for r in unique[:_MAX_EXCERPTS]],
        }


def calculation(operation: str, inputs: list, result, description: str, unit: str = "") -> dict:
    """A calculation the validator recomputes: operation is sum | mean | range | difference_days;
    unit is amount | days | minutes (for display)."""
    return {"operation": operation, "inputs": inputs, "result": result, "description": description, "unit": unit}


# ── Wording helpers ───────────────────────────────────────────────────────────

def person_label(person: Entity | None) -> str:
    if person is None:
        return "an unidentified user"
    return f"{person.name} ({person.title})" if person.title else person.name


def day(when: datetime, with_time: bool = False) -> str:
    return when.strftime("%d %b %Y, %H:%M" if with_time else "%d %b %Y")


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def join_words(words: list[str]) -> str:
    words = list(dict.fromkeys(words))
    if not words:
        return ""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + f" and {words[-1]}"
