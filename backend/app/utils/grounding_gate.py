"""
utils/grounding_gate.py
-----------------------
Grounding Gate — post-generation evidence validation for the Report Generator.

Checks the final report for evidence grounding violations BEFORE the report
is emitted to the frontend. Each finding, timeline event, connection, and
person entry must be linked to at least one valid evidence ID that was
actually indexed by the Evidence Collector.

Rules enforced:
  1. No hallucinated evidence IDs — every ID referenced must be in the
     valid_evidence_ids set returned by the collector.
  2. No ungrounded findings — every finding must cite at least one valid ID.
  3. No self-connections — a connection may not link an evidence ID to itself.
  4. No empty persons — a person entry must have at least one evidence reference.
  5. No accusatory language — findings/events must not contain direct guilt claims.

The gate never hard-blocks the report. It annotates violations and the pipeline
logs them in telemetry. The report is marked as degraded only if a configurable
severity threshold is exceeded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Accusatory language pattern (mirrors report_generator.py) ─────────────────

_ACCUSATORY_RE = re.compile(
    r"\b(guilty|culprit|criminal|offender|perpetrator|committed|responsible for|"
    r"stole|murdered|killed|fraudster|suspect is|suspect was)\b",
    re.IGNORECASE,
)

_EVIDENCE_ID_RE = re.compile(r"\bE-\d{3,}\b", re.IGNORECASE)


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class GateViolation:
    """A single grounding violation found in the report."""
    rule: str           # e.g. "hallucinated_evidence_id"
    section: str        # e.g. "findings", "timeline", "connections", "persons"
    item_index: int     # 0-based index of the offending item in its section
    detail: str         # Human-readable description


@dataclass
class GateResult:
    """Result of running the grounding gate over a final report."""
    violations: list[GateViolation] = field(default_factory=list)
    items_checked: int = 0
    items_passed: int = 0

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    def summary(self) -> str:
        if self.passed:
            return f"Gate passed. {self.items_checked} item(s) checked, 0 violations."
        return (
            f"Gate found {self.violation_count} violation(s) across "
            f"{self.items_checked} checked item(s)."
        )

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "violation_count": self.violation_count,
            "items_checked": self.items_checked,
            "items_passed": self.items_passed,
            "violations": [
                {
                    "rule": v.rule,
                    "section": v.section,
                    "item_index": v.item_index,
                    "detail": v.detail,
                }
                for v in self.violations
            ],
        }


# ── Gate implementation ────────────────────────────────────────────────────────

def run_grounding_gate(
    report: dict,
    *,
    valid_evidence_ids: set[str],
) -> GateResult:
    """Run all grounding checks over the generated report dict.

    Args:
        report: The final report dict as returned by the report_generator.
        valid_evidence_ids: Set of evidence IDs confirmed indexed by the collector.

    Returns:
        GateResult describing any violations found.
    """
    result = GateResult()

    _check_findings(report, valid_evidence_ids, result)
    _check_timeline(report, valid_evidence_ids, result)
    _check_connections(report, valid_evidence_ids, result)
    _check_persons(report, valid_evidence_ids, result)

    result.items_passed = result.items_checked - result.violation_count
    return result


def _check_findings(
    report: dict,
    valid_ids: set[str],
    result: GateResult,
) -> None:
    findings = report.get("findings", [])
    if not isinstance(findings, list):
        return
    for i, finding in enumerate(findings):
        result.items_checked += 1
        text = str(finding) if isinstance(finding, str) else str(finding.get("text", "") if isinstance(finding, dict) else finding)
        refs = _extract_ids_from_finding(finding, valid_ids)

        if not refs:
            result.violations.append(GateViolation(
                rule="ungrounded_finding",
                section="findings",
                item_index=i,
                detail=f"Finding at index {i} has no valid evidence ID references.",
            ))
        else:
            hallucinated = [r for r in _all_ids_in_text(text) if r not in valid_ids]
            for bad_id in hallucinated:
                result.violations.append(GateViolation(
                    rule="hallucinated_evidence_id",
                    section="findings",
                    item_index=i,
                    detail=f"Finding at index {i} references non-existent evidence ID '{bad_id}'.",
                ))

        if _ACCUSATORY_RE.search(text):
            result.violations.append(GateViolation(
                rule="accusatory_language",
                section="findings",
                item_index=i,
                detail=f"Finding at index {i} contains accusatory language.",
            ))


def _check_timeline(
    report: dict,
    valid_ids: set[str],
    result: GateResult,
) -> None:
    events = report.get("timeline", [])
    if not isinstance(events, list):
        return
    for i, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        result.items_checked += 1
        refs = _clean_id_list(event.get("refs") or event.get("evidence_ids") or [], valid_ids)
        if not refs:
            result.violations.append(GateViolation(
                rule="ungrounded_timeline_event",
                section="timeline",
                item_index=i,
                detail=f"Timeline event at index {i} has no valid evidence references.",
            ))
        event_text = str(event.get("event", ""))
        if _ACCUSATORY_RE.search(event_text):
            result.violations.append(GateViolation(
                rule="accusatory_language",
                section="timeline",
                item_index=i,
                detail=f"Timeline event at index {i} contains accusatory language.",
            ))


def _check_connections(
    report: dict,
    valid_ids: set[str],
    result: GateResult,
) -> None:
    connections = report.get("connections", [])
    if not isinstance(connections, list):
        return
    for i, conn in enumerate(connections):
        if not isinstance(conn, dict):
            continue
        result.items_checked += 1
        a = _normalise_id(conn.get("a", ""))
        b = _normalise_id(conn.get("b", ""))
        if not a or a not in valid_ids:
            result.violations.append(GateViolation(
                rule="hallucinated_evidence_id",
                section="connections",
                item_index=i,
                detail=f"Connection at index {i}: 'a' value '{a}' is not a valid evidence ID.",
            ))
        if not b or b not in valid_ids:
            result.violations.append(GateViolation(
                rule="hallucinated_evidence_id",
                section="connections",
                item_index=i,
                detail=f"Connection at index {i}: 'b' value '{b}' is not a valid evidence ID.",
            ))
        if a and b and a == b:
            result.violations.append(GateViolation(
                rule="self_connection",
                section="connections",
                item_index=i,
                detail=f"Connection at index {i} links evidence ID '{a}' to itself.",
            ))


def _check_persons(
    report: dict,
    valid_ids: set[str],
    result: GateResult,
) -> None:
    persons = report.get("persons", [])
    if not isinstance(persons, list):
        return
    for i, person in enumerate(persons):
        if not isinstance(person, dict):
            continue
        result.items_checked += 1
        name = str(person.get("name", "")).strip()

        # Support both internal format (evidence_ids/evidence_refs) and
        # frontend format (IDs embedded in 'note' text via _append_refs)
        refs = _clean_id_list(
            person.get("evidence_ids") or person.get("evidence_refs") or [],
            valid_ids,
        )
        if not refs:
            # Frontend format: IDs are appended to the note text as [E-XXX]
            note_text = str(person.get("note", ""))
            refs = _clean_id_list(_all_ids_in_text(note_text), valid_ids)

        if not name:
            result.violations.append(GateViolation(
                rule="unnamed_person",
                section="persons",
                item_index=i,
                detail=f"Person at index {i} has no name.",
            ))
        if not refs:
            result.violations.append(GateViolation(
                rule="ungrounded_person",
                section="persons",
                item_index=i,
                detail=f"Person '{name}' at index {i} has no valid evidence references.",
            ))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalise_id(value: object) -> str:
    match = _EVIDENCE_ID_RE.search(str(value or "").upper())
    return match.group(0).upper() if match else ""


def _all_ids_in_text(text: str) -> list[str]:
    return [m.upper() for m in _EVIDENCE_ID_RE.findall(text)]


def _clean_id_list(values: object, valid_ids: set[str]) -> list[str]:
    if isinstance(values, str):
        values = _EVIDENCE_ID_RE.findall(values)
    if not isinstance(values, list):
        return []
    result = []
    for v in values:
        ref = _normalise_id(v)
        if ref and (not valid_ids or ref in valid_ids):
            result.append(ref)
    return result


def _extract_ids_from_finding(finding: object, valid_ids: set[str]) -> list[str]:
    """Extract valid evidence IDs from a finding (str or dict)."""
    if isinstance(finding, str):
        return _clean_id_list(_EVIDENCE_ID_RE.findall(finding), valid_ids)
    if isinstance(finding, dict):
        refs = finding.get("evidence_ids") or finding.get("refs") or []
        if not refs:
            text = str(finding.get("text", ""))
            refs = _EVIDENCE_ID_RE.findall(text)
        return _clean_id_list(refs, valid_ids)
    return []
