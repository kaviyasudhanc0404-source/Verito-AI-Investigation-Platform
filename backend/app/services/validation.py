"""
services/validation.py
----------------------
Independent checks applied to every finding before it reaches the report, and
the configurable quality gate.

    findings, rejected = validate_findings(case, observations, document_facts)
    gate = quality_gate(metrics)

Each finding is re-checked against the evidence, not against the rule that made it:
  • every cited file exists in this case
  • a quoted statement appears verbatim in its source
  • every identifier, amount and date in the text appears in the cited sources
    (or is the result of the finding's own calculation)
  • the calculation recomputes to the stated result
  • the wording does not assert guilt or intent
Findings that fail are withheld with their reasons. Duplicates are merged. Each
accepted finding gets a claim type, a confidence level and the basis for it:

  Established by the records   documented, ≥2 independent sources, identities certain
  Documented observation       documented in the records
  Pattern requiring review     an inference from several data points
  Reported statement           quoted from a narrative document
"""

from __future__ import annotations

import re
from statistics import mean

from app.core.config import get_settings
from app.services.case_model import Case
from app.services.reconciliation.base import SEVERITY_CRITERIA, SEVERITY_ORDER, Observation
from app.services.values import find_amounts, find_dates, find_ids, normalise_space, parse_amount, parse_timestamp

ACCUSATORY = re.compile(
    r"\b(guilty|culprit|criminal|fraudster|perpetrator|embezzl\w*|stole|theft|committed fraud|corrupt\w*|"
    r"intentionally|deliberately|conspir\w*|kickback|bribe\w*)\b", re.IGNORECASE)
_CLAIMS = {
    "established": "Established by the records",
    "documented": "Documented observation",
    "pattern": "Pattern requiring review",
    "statement": "Reported statement",
}
_LEVELS = ("low", "medium", "high")


# ── Findings ──────────────────────────────────────────────────────────────────

def validate_findings(case: Case, observations: list[Observation], document_facts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (accepted findings, rejected findings with reasons)."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: dict[str, dict] = {}
    for candidate in [*map(_from_observation, observations), *(_from_statement(case, f) for f in document_facts)]:
        key = re.sub(r"\W+", " ", candidate["title"].casefold()).strip()
        if key in seen:                                   # duplicate wording → one finding, all citations
            seen[key]["refs"] = sorted(set(seen[key]["refs"]) | set(candidate["refs"]))
            continue
        problems = _problems(case, candidate)
        if problems:
            rejected.append({"id": candidate["id"], "title": candidate["title"], "reasons": problems})
            continue
        _assess(case, candidate)
        seen[key] = candidate
        accepted.append(candidate)
    accepted.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 3), f["source"] != "rule"))
    return accepted, rejected


def _from_observation(o: Observation) -> dict:
    finding = o.to_dict()
    finding.update(source="rule", uncertain=[], _records=o.records)
    return finding


def _from_statement(case: Case, fact: dict) -> dict:
    text = normalise_space(fact["text"])
    return {
        "id": fact["id"], "title": text if len(text) <= 320 else text[:319] + "…",
        "detail": f"Stated in {case.filename(fact['evidence_id'])}.", "severity": "medium", "category": "Documents",
        "refs": [fact["evidence_id"]], "subjects": [], "basis": "statement", "rule": "document_statement",
        "why": "", "question": "", "verify": ["Confirm the statement with its author or with the underlying records."],
        "qualifiers": [], "calculation": None, "records": [], "excerpts": [
            {"ref": fact["evidence_id"], "location": case.filename(fact["evidence_id"]), "text": text}],
        "source": "document", "quote": fact["text"], "uncertain": [], "_records": [],
    }


def _problems(case: Case, finding: dict) -> list[str]:
    problems = []
    missing = [ref for ref in finding["refs"] if ref not in case.evidence_ids]
    if not finding["refs"] or missing:
        problems.append(f"cites evidence that is not in this case ({', '.join(missing) or 'none cited'})")
        return problems
    sources = " ".join(case.text_of(ref) for ref in finding["refs"])
    text = f"{finding['title']} {finding['detail']}"

    if finding["source"] == "document":
        if _squash(finding["quote"]) not in _squash(sources):
            problems.append("the quoted statement was not found in its source")
        return problems

    unknown_ids = [i for i in find_ids(text) if i not in sources]
    if unknown_ids:
        problems.append(f"mentions {', '.join(unknown_ids)}, which the cited sources do not contain")

    calc = finding.get("calculation")
    calc_values = set()
    if calc:
        recomputed = _recompute(calc)
        if recomputed is None or not _close(recomputed, calc["result"]):
            problems.append(f"the calculation '{calc['description']}' does not recompute to {calc['result']}")
        calc_values = {v for v in _flat(calc["result"]) if isinstance(v, (int, float))}

    known_amounts = set(find_amounts(sources)) | calc_values | {
        a for r in finding["_records"] for v in r.fields.values() if (a := parse_amount(v)) is not None}
    unknown_amounts = [a for a in find_amounts(text) if not any(_close(a, k) for k in known_amounts)]
    if unknown_amounts:
        problems.append(f"states amounts not found in the cited sources ({', '.join(f'{a:,.0f}' for a in unknown_amounts)})")

    known_days = {m.when.date() for m in find_dates(sources)} | {
        stamp.local.date() for r in finding["_records"] for _, _, stamp in r.dates()}
    unknown_days = [m.text for m in find_dates(text) if m.when.date() not in known_days]
    if unknown_days:
        problems.append(f"states dates not found in the cited sources ({', '.join(unknown_days)})")

    if ACCUSATORY.search(text):
        problems.append("uses language that asserts guilt or intent")
    return problems


def _assess(case: Case, finding: dict) -> None:
    """Set claim type, confidence and the basis for both; downgrade severity when support is weak."""
    records = finding.pop("_records")
    refs = finding["refs"]
    independent = case.independent_sources(refs)
    notes = []
    copies = [(a, b) for a, b, _ in case.copies if a in refs and b in refs]
    if copies:
        notes.append("; ".join(f"{a} and {b} hold copies of the same records, so they count as one source"
                               for a, b in copies))
    finding["uncertain"] = []
    for key in finding["subjects"]:
        entity = case.registry.get(key)
        aliases = sorted(a for a in case.registry.uncertain_aliases.get(key, ()) if any(a in r.text() for r in records))
        if entity and aliases:
            finding["uncertain"].append(
                f"{len(aliases)} reference{'s' if len(aliases) > 1 else ''} to {entity.name} (e.g. '{aliases[0]}') "
                "matched by partial name, initials or email domain only")
    uncertain = bool(finding["uncertain"])
    notes += finding["uncertain"]

    basis = finding["basis"]
    level = {"documented": 2, "pattern": 1, "statement": 0}[basis] + (independent >= 2) - bool(uncertain)
    level = max(0, min(2, level))
    if basis == "statement" and _corroborated(case, finding):
        level = 1
        notes.append("the identifiers it mentions also appear in other evidence")
    finding["confidence"] = _LEVELS[level]
    finding["confidence_basis"] = "; ".join([
        f"{_basis_text(basis)} in {independent} independent source{'s' if independent != 1 else ''}", *notes])
    claim = "established" if basis == "documented" and level == 2 and not uncertain else basis
    finding["claim"] = _CLAIMS[claim]

    status, adjustments = "passed", []
    if finding["severity"] == "high" and finding["confidence"] == "low":
        finding["severity"] = "medium"
        status = "adjusted"
        adjustments.append("severity lowered from high to medium because the supporting evidence is weak")
    finding["severity_basis"] = SEVERITY_CRITERIA[finding["severity"]]
    if finding.get("calculation"):
        finding["calculation"] = {**finding["calculation"], "text": _describe(case, finding["calculation"])}
    finding["validation"] = {"status": status, "notes": adjustments}


def _corroborated(case: Case, finding: dict) -> bool:
    ids = find_ids(finding["title"])
    others = [item.text for item in case.evidence if item.evidence_id not in finding["refs"]]
    return bool(ids) and all(any(i in text for text in others) for i in ids)


def _basis_text(basis: str) -> str:
    return {"documented": "Stated directly by the records", "pattern": "Inferred from records",
            "statement": "Reported"}[basis]


# ── Calculations ──────────────────────────────────────────────────────────────

def _recompute(calc: dict):
    inputs, operation = calc["inputs"], calc["operation"]
    try:
        if operation == "sum":
            return sum(inputs)
        if operation == "mean":
            return round(mean(inputs), 1)
        if operation == "range":
            return [min(inputs), max(inputs)]
        if operation == "difference_days":
            start, end = (parse_timestamp(str(v)).local for v in inputs)
            return (end - start).days
    except (TypeError, ValueError, AttributeError):
        return None
    return None


def _describe(case: Case, calc: dict) -> str:
    unit = calc.get("unit", "")

    def show(value) -> str:
        if unit == "amount":
            return case.money(value)
        return f"{value:g}" + (f" {unit[:-1] if value == 1 else unit}" if unit else "")

    result, inputs = calc["result"], calc["inputs"]
    if calc["operation"] == "difference_days":
        listed = " → ".join(parse_timestamp(str(v)).display() for v in inputs)
    else:
        listed = ", ".join(show(v) for v in inputs[:8]) + (" …" if len(inputs) > 8 else "")
    if not isinstance(result, list):
        shown = show(result)
    elif unit == "amount":
        shown = "–".join(map(show, result))
    else:
        shown = f"{result[0]:g}–{show(result[1])}"         # "5–8 minutes"
    return f"{calc['description']}: {shown} ({calc['operation'].replace('_', ' ')} of {listed})"


def _close(a, b) -> bool:
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(map(_close, a, b))
    try:
        return abs(float(a) - float(b)) <= max(0.51, abs(float(b)) * 1e-6)
    except (TypeError, ValueError):
        return False


def _flat(value) -> list:
    return value if isinstance(value, list) else [value]


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").casefold())


# ── Quality gate ──────────────────────────────────────────────────────────────

def quality_gate(*, files_total: int, files_failed: int, narrative_chunks: int, fallback_chunks: int,
                 rejected: int, findings: int, events: int) -> dict:
    """Compare run metrics with the configured thresholds. Status: passed | warning | partial | blocked."""
    settings = get_settings()
    failed_ratio = files_failed / files_total if files_total else 1.0
    fallback_ratio = fallback_chunks / narrative_chunks if narrative_chunks else 0.0
    reasons = []
    if failed_ratio > settings.quality_max_failed_file_ratio:
        reasons.append(f"{files_failed} of {files_total} evidence files could not be read "
                       f"(limit {settings.quality_max_failed_file_ratio:.0%}).")
    if fallback_ratio > settings.quality_max_fallback_ratio:
        reasons.append(f"{fallback_chunks} of {narrative_chunks} document sections were analysed without AI "
                       f"extraction (limit {settings.quality_max_fallback_ratio:.0%}).")
    if rejected > settings.quality_max_rejected_findings:
        reasons.append(f"{rejected} finding(s) failed validation and were withheld "
                       f"(limit {settings.quality_max_rejected_findings}).")
    if not findings and not events:
        reasons.append("No findings or dated events could be derived from the evidence.")

    policy = settings.quality_gate_policy if settings.quality_gate_policy in {"warn", "partial", "block"} else "partial"
    status = "passed" if not reasons else {"warn": "warning", "partial": "partial", "block": "blocked"}[policy]
    return {
        "status": status, "policy": policy, "reasons": reasons,
        "metrics": {"files_total": files_total, "files_failed": files_failed, "fallback_ratio": round(fallback_ratio, 2),
                    "rejected_findings": rejected, "findings": findings, "events": events},
    }
