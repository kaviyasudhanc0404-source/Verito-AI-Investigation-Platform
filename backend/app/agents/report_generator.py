"""
agents/report_generator.py
----------------------------
Agent 07 — Report Generator.

Writes the final report for readers with no technical or legal background. The
sections adapt to the case — anything with no content is left out:

  title · status · summary · executive summary (what the records establish / suggest /
  leave uncertain / what to verify next) · at a glance · scope · findings · reconciliation
  results · key events · people · connections · open questions · next steps ·
  evidence inventory · limitations · methodology · quality status

Findings come only from the Finding Validator, so every one is cited, re-checked
and graded. The LLM adds only the wording a reader needs: a title, one scope
sentence, one caveat, open questions and next steps — all checked for neutral
language. Without the LLM the same report is written from the rules.
"""

from __future__ import annotations

import json
from collections import Counter

from pydantic import BaseModel

from app.agents.base import AgentResult, AgentRun, PipelineContext
from app.agents.subject_analyzer import INVOLVEMENT_CRITERIA
from app.agents.timeline_builder import CATEGORY_WEIGHT
from app.core.config import get_settings
from app.services.case_model import Case
from app.services.llm_service import chat_completion_json, failure_reason
from app.services.records import kind_name
from app.services.reconciliation import SEVERITY_CRITERIA, adapters_used
from app.services.report_service import save_report
from app.services.validation import ACCUSATORY
from app.utils.datetime_utils import utcnow

_SYSTEM_PROMPT = """You help write the final report of an internal investigation for readers with no technical or legal background.

Use short, plain, neutral English and only the facts provided. State exact counts and dates; never generalise one case into many.
Mention only issues that appear in findings — do not add new ones.
Never say anyone is guilty, corrupt, committed fraud, or acted intentionally — describe what the records show and what still needs confirming.

Return JSON:
{
  "title": "max 8 words naming the matter",
  "scope": "one sentence: what was examined (which records, which parties, which period)",
  "caveat": "one sentence: what the evidence does not yet establish",
  "open_questions": ["max 6 questions the evidence raises but does not answer"],
  "next_steps": ["max 6 concrete, neutral actions for the investigator"]
}"""

_MAX_EVENTS, _MAX_LIST = 40, 6
_FILE_LABELS = {
    "pdf": "PDF document", "docx": "Word document", "doc": "Word document", "xlsx": "Spreadsheet", "xls": "Spreadsheet",
    "csv": "Spreadsheet (CSV)", "json": "Data export (JSON)", "xml": "Data export (XML)", "eml": "Email file",
    "msg": "Email file", "txt": "Text file", "pptx": "Presentation", "zip": "Archive",
}
_FINDING_KEYS = ("id", "title", "detail", "why", "severity", "severity_basis", "category", "claim", "confidence",
                 "confidence_basis", "refs", "records", "excerpts", "calculation", "qualifiers", "verify", "question",
                 "source")
_DEFAULT_CAVEAT = ("These are patterns in the records that need to be confirmed with the people involved; "
                   "on their own they do not establish wrongdoing or intent.")


class _Narrative(BaseModel):
    title: str = ""
    scope: str = ""
    caveat: str = ""
    open_questions: list[str] = []
    next_steps: list[str] = []


async def run(ctx: PipelineContext, agent: AgentRun) -> AgentResult:
    case: Case = ctx.case
    outputs = ctx.outputs
    validator, timeline = outputs.get("validator", {}), outputs.get("timeline", {})
    linker, subjects = outputs.get("linker", {}), outputs.get("subject", {})
    findings = [{k: f.get(k) for k in _FINDING_KEYS} for f in validator.get("findings", [])]
    flags = [f for f in findings if f["source"] == "rule"]
    people = subjects.get("persons", [])
    focus = _focus(case, validator.get("findings", []))
    key_events = _key_events(timeline.get("events", []), flags)
    gate = validator.get("quality", {"status": "passed", "reasons": []})

    await agent.log("scan", "Writing the case narrative...")
    rules = _rule_narrative(flags, findings, focus)
    narrative, problem = await _narrative(ctx.prompt, case, findings, people, key_events, rules)
    if problem:
        await agent.fallback(f"AI writing unavailable ({problem}) — report written from the rule-based findings.")
    generated_by = "rules" if problem else "ai"

    limitations = _limitations(ctx, case, generated_by)
    report = {
        "title": narrative["title"],
        "status": "partial" if gate["status"] == "partial" else "final",
        "summary": _summary(case, flags, focus, narrative["scope"], narrative["caveat"]),
        "executive_summary": _executive_summary(findings, people, limitations, narrative["caveat"]),
        "metadata": _metadata(ctx, case, timeline, generated_by),
        "at_a_glance": _at_a_glance(case, flags, people, timeline, focus),
        "scope": _scope(case, narrative["scope"], timeline, ctx.prompt),
        "findings": findings,
        "reconciliation": _reconciliation(case, flags, linker),
        "timeline": key_events,
        "all_events": timeline.get("events", []),
        "people": [{k: p.get(k, "") for k in ("name", "role", "category", "involvement", "involvement_basis", "note",
                                               "context", "identity_note", "refs")} for p in people],
        "connections": [{k: c.get(k) for k in ("a", "b", "a_name", "b_name", "type", "method", "reason", "strength",
                                                "excerpts")} for c in linker.get("links", [])],
        "open_questions": narrative["open_questions"],
        "next_steps": narrative["next_steps"],
        "evidence": _evidence_inventory(ctx, case),
        "limitations": limitations,
        "methodology": _methodology(case, subjects),
        "quality": gate,
        "withheld": validator.get("rejected", []),
        "diagnostics": ctx.diagnostics,
        "generated_by": generated_by,
        "generated_at": utcnow().isoformat(),
    }
    await save_report(ctx.investigation_id, report)
    await agent.log("ok", f"Report saved: {len(findings)} findings, {len(key_events)} key events"
                          + (" (partial — see quality notes)." if report["status"] == "partial" else "."))
    summary = [f"{len(findings)} findings", f"{len(key_events)} key events"]
    if report["status"] == "partial":
        summary.append("Partial report")
    return AgentResult({"title": report["title"], "generated_by": generated_by, "status": report["status"]},
                       summary, agent.status, counts={"findings": len(findings), "key_events": len(key_events)})


# ── Narrative (LLM with rule-based fallback) ──────────────────────────────────

async def _narrative(prompt: str, case: Case, findings: list[dict], people: list[dict], key_events: list[dict],
                     rules: dict) -> tuple[dict, str]:
    """Return (narrative, problem). `problem` is empty when the LLM narrative was used."""
    context = {
        "investigation_question": prompt or "Not specified — review the evidence for anything that needs attention.",
        "evidence_files": [f"{e.evidence_id}: {e.filename}" for e in case.evidence],
        "findings": [{k: f[k] for k in ("id", "title", "detail", "severity", "claim")} for f in findings],
        "people": [{"name": p["name"], "role": p["role"], "involvement": p["involvement"]} for p in people[:10]],
        "key_events": [f"{e['date']} {e['time']}".strip() + f": {e['event']}" for e in key_events[:20]],
    }
    try:
        reply = await chat_completion_json(
            [{"role": "system", "content": _SYSTEM_PROMPT},
             {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
            temperature=0.2, max_tokens=2000, reasoning_effort="medium", purpose="report.narrative", schema=_Narrative,
        )
    except Exception as exc:
        return rules, failure_reason(exc)

    scope, caveat = _clean(reply["scope"], 300), _clean(reply["caveat"], 300)
    if not scope or not caveat or ACCUSATORY.search(f"{scope} {caveat} {reply['title']}"):
        return rules, "the draft did not meet the neutral-language rules"
    return {
        "title": _clean(reply["title"], 80) or rules["title"],
        "scope": scope,
        "caveat": caveat,
        "open_questions": _clean_list(reply["open_questions"]) or rules["open_questions"],
        "next_steps": _clean_list(reply["next_steps"]) or rules["next_steps"],
    }, ""


def _rule_narrative(flags: list[dict], findings: list[dict], focus: tuple[str, str]) -> dict:
    name, kind = focus
    title = {"organisation": f"Review of dealings with {name}", "account": f"Review of activity on {name}",
             "person": f"Review of matters involving {name}"}.get(kind, "Evidence review")
    return {
        "title": title,
        "scope": "",
        "caveat": _DEFAULT_CAVEAT,
        "open_questions": _unique(f.get("question") for f in flags)[:_MAX_LIST],
        "next_steps": _unique(step for f in findings for step in f["verify"][:1])[:_MAX_LIST],
    }


def _summary(case: Case, flags: list[dict], focus: tuple[str, str], scope: str, caveat: str) -> str:
    """Scope sentence + what the evidence shows (the top findings in their own exact wording) + caveat."""
    files = f"{len(case.evidence)} evidence file{'s' if len(case.evidence) != 1 else ''}"
    scope = scope or f"Verito reviewed {files}{f' concerning {focus[0]}' if focus[0] else ''}."
    if not flags:
        return f"{scope} No cross-evidence red flags were detected; the key facts from the documents are listed below."
    counts = Counter(f["severity"] for f in flags)
    top = [f["title"] for f in flags[:3]]
    listed = top[0] if len(top) == 1 else "; ".join(top[:-1]) + f"; and {top[-1]}"
    return (f"{scope} The records show {len(flags)} red flag{'s' if len(flags) > 1 else ''} "
            f"({counts['high']} high priority). The most significant: {listed}. {caveat}")


def _executive_summary(findings: list[dict], people: list[dict], limitations: list[str], caveat: str) -> dict:
    uncertain = [caveat] + [f"{p['name']}: {p['identity_note']}" for p in people if p.get("identity_note")][:3]
    uncertain += [f"{f['id']}: confidence is {f['confidence']} — {f['confidence_basis']}."
                  for f in findings if f["confidence"] == "low" and f["source"] == "rule"][:3]
    uncertain += limitations[:3]
    return {
        "establishes": [f"{f['title']} ({f['id']})" for f in findings if f["claim"] == "Established by the records"][:_MAX_LIST],
        "suggests": [f"{f['title']} ({f['id']})" for f in findings
                     if f["source"] == "rule" and f["claim"] != "Established by the records"][:_MAX_LIST],
        "uncertain": _unique(uncertain)[:_MAX_LIST],
        "verify_next": _unique(step for f in findings if f["source"] == "rule" for step in f["verify"][:1])[:_MAX_LIST],
    }


# ── Deterministic sections ────────────────────────────────────────────────────

def _key_events(events: list[dict], flags: list[dict]) -> list[dict]:
    """The events a reader needs: weighted by type and by records cited in findings, shown in date order."""
    cited = {record for f in flags for record in f.get("records") or []}
    scored = []
    for index, event in enumerate(events):
        in_finding = bool(cited & set(event.get("records", [])))
        if event["category"] == "Access" and not in_finding and "(" not in event["event"]:
            continue                                   # routine sign-in / sign-out
        scored.append((CATEGORY_WEIGHT.get(event["category"], 1) + 3 * in_finding, index, event))
    chosen = sorted(scored, key=lambda item: (-item[0], item[1]))[:_MAX_EVENTS]
    return [event for _, _, event in sorted(chosen, key=lambda item: item[1])]


def _at_a_glance(case: Case, flags: list[dict], people: list[dict], timeline: dict, focus: tuple[str, str]) -> list[dict]:
    counts = Counter(f["severity"] for f in flags)
    severities = " · ".join(f"{counts[s]} {s}" for s in ("high", "medium", "low") if counts[s]) or "None detected"
    review = [p["name"] for p in people if p["involvement"] == "High"]
    rows = [
        {"label": "Evidence reviewed", "value": f"{len(case.evidence)} files · {len(case.records)} records"},
        {"label": "Period covered", "value": timeline.get("window") or "Not determined"},
        {"label": "Red flags", "value": severities},
        {"label": "Require further review", "value": ", ".join(review) if review else "No one"},
    ]
    name, kind = focus
    if name:
        rows.append({"label": "Main party of concern", "value": name})
        if kind == "organisation":
            by_kind: dict[str, list[float]] = {}
            for doc in case.documents.values():
                if case.name(doc.value("vendor")) == name and doc.amount() is not None and not case.is_party(doc.doc_id):
                    by_kind.setdefault(doc.kind, []).append(doc.amount())
            for doc_kind, amounts in by_kind.items():
                rows.append({"label": f"Value of {kind_name(doc_kind, 2)}",
                             "value": f"{case.money(sum(amounts))} across {len(amounts)} {kind_name(doc_kind, len(amounts))}"})
    return rows


def _focus(case: Case, findings: list[dict]) -> tuple[str, str]:
    """(name, kind) of the party named most often in the findings — organisations first."""
    named = Counter(key for f in findings for key in f.get("subjects", []) if case.registry.get(key))
    for kind in ("organisation", "account", "person"):
        ranked = [(n, key) for key, n in named.items() if case.registry.get(key).kind == kind]
        if ranked:
            return case.registry.get(max(ranked)[1]).name, kind
    return "", ""


def _scope(case: Case, scope_sentence: str, timeline: dict, prompt: str) -> list[str]:
    kinds = Counter(r.kind for r in case.records if r.kind != "generic")
    contents = ", ".join(f"{n} {kind_name(k, n)}" for k, n in kinds.most_common(6))
    items = [scope_sentence] if scope_sentence else []
    if prompt:
        items.append(f"Question examined: {prompt}")
    items.append(f"{len(case.evidence)} evidence files were analysed" + (f", containing {contents}." if contents else "."))
    if timeline.get("window"):
        items.append(f"The dated records cover {timeline['window']}.")
    return items


def _reconciliation(case: Case, flags: list[dict], linker: dict) -> list[dict]:
    """How the sources were lined up against each other, and what that showed."""
    shared = [d for d in case.documents.values() if len(d.refs) > 1]
    conflict_flags = [f for f in flags if f["category"] in {"Record consistency", "Chronology", "Duplicates", "Missing records"}]
    rows = [{"check": "Checks applied",
             "result": "Generic consistency checks" + "".join(f"; {name}" for name in linker.get("adapters", [])),
             "refs": []}]
    if shared:
        rows.append({"check": "Documents found in more than one file",
                     "result": f"{len(shared)} documents were matched across files by their own ID.",
                     "refs": sorted({r for d in shared for r in d.refs})})
    for a, b, count in case.copies:
        rows.append({"check": "Duplicate sources",
                     "result": f"{a} and {b} hold the same {count} records; they are treated as one source.",
                     "refs": [a, b]})
    for f in conflict_flags:
        rows.append({"check": f["category"], "result": f"{f['title']} — see {f['id']}.", "refs": f["refs"]})
    if not conflict_flags:
        rows.append({"check": "Conflicts", "result": "No conflicting values, duplicates or out-of-order dates were found.",
                     "refs": []})
    return rows


def _evidence_inventory(ctx: PipelineContext, case: Case) -> list[dict]:
    files = ctx.outputs.get("collector", {}).get("files", {}) or {i.evidence_id: i.ingestion for i in case.evidence}
    kinds_by_file: dict[str, Counter] = {}
    for record in case.records:
        if record.kind != "generic":
            kinds_by_file.setdefault(record.evidence_id, Counter())[record.kind] += 1
    inventory = []
    for evidence_id, info in sorted(files.items()):
        name = info.get("filename") or case.filename(evidence_id)
        label = _FILE_LABELS.get(info.get("format") or name.rsplit(".", 1)[-1].lower(), "File")
        contents = ", ".join(f"{n} {kind_name(k, n)}" for k, n in kinds_by_file.get(evidence_id, Counter()).most_common(3))
        inventory.append({
            "id": evidence_id, "name": name, "description": f"{label} — {contents}" if contents else label,
            "status": info.get("status", "indexed"), "records": info.get("records", 0), "chunks": info.get("chunks", 0),
            "notes": ([info["error"]] if info.get("error") else []) + list(info.get("warnings", [])),
        })
    return inventory


def _limitations(ctx: PipelineContext, case: Case, generated_by: str) -> list[str]:
    outputs, notes = ctx.outputs, []
    files = outputs.get("collector", {}).get("files", {})
    unread = [f"{eid} ({info.get('filename', '')})" for eid, info in files.items() if not info["status"].startswith("indexed")]
    if unread:
        notes.append(f"{len(unread)} file(s) could not be read and were not analysed: {', '.join(unread)}.")
    ocr = [eid for eid, info in files.items() if info.get("ocr")]
    if ocr:
        notes.append(f"Text in {', '.join(ocr)} was read with OCR and may contain recognition errors.")
    fallback = outputs.get("analyzer", {}).get("chunks", {}).get("fallback", 0)
    if fallback:
        notes.append(f"{fallback} document section(s) were analysed with rules only because the AI service did not respond.")
    counts = outputs.get("timeline", {}).get("counts", {})
    if counts.get("unzoned") and case.offset is not None:
        notes.append(f"{counts['unzoned']} timed event(s) state no timezone; they were ordered assuming the case's main timezone.")
    if counts.get("approximate"):
        notes.append(f"{counts['approximate']} event date(s) are approximate (marked ~).")
    if counts.get("left_out"):
        notes.append(f"{counts['left_out']} reference date(s) far outside the period of activity are not shown on the timeline.")
    withheld = outputs.get("validator", {}).get("rejected", [])
    if withheld:
        notes.append(f"{len(withheld)} candidate finding(s) failed validation and were withheld.")
    notes += outputs.get("validator", {}).get("quality", {}).get("reasons", [])
    if generated_by == "rules":
        notes.append("The title, questions and next steps were written from the rules because the AI writing service was unavailable.")
    return _unique(notes)


def _metadata(ctx: PipelineContext, case: Case, timeline: dict, generated_by: str) -> dict:
    return {
        "investigation_id": ctx.investigation_id,
        "question": ctx.prompt,
        "evidence_files": len(case.evidence),
        "records": len(case.records),
        "period": timeline.get("window", ""),
        "checks": ["Generic consistency checks", *adapters_used(case)],
        "model": get_settings().groq_model if generated_by == "ai" else "",
    }


def _methodology(case: Case, subjects: dict) -> list[str]:
    settings = get_settings()
    checks = ", ".join(["generic consistency checks (conflicting values, duplicates, chronology, missing records)",
                        *adapters_used(case)])
    return [
        "Text was extracted from every file (Docling for documents, OCR for images, speech-to-text for audio); "
        "each file's processing status is listed in the evidence inventory.",
        "Structured rows were read field by field, and each finding cites the exact rows it rests on.",
        f"Cross-checks applied: {checks}.",
        "Every finding was re-checked against its sources before inclusion: identifiers, amounts and dates must "
        "appear in the cited files, quotes must be verbatim, and calculations must recompute.",
        "Copies of the same data (for example a PDF table and a spreadsheet export) count as one source.",
        *(f"Severity — {level}: {text}" for level, text in SEVERITY_CRITERIA.items()),
        "Confidence — high: stated directly by the records in two or more independent sources with certain identities; "
        "medium: one source, or an inference supported by several; low: a single inferred or reported item, or "
        "identities matched only by partial name.",
        *(f"Involvement — {level}: {text}" for level, text in subjects.get("criteria", INVOLVEMENT_CRITERIA).items()),
        f"An AI model ({settings.groq_model}) was used only to extract names and quotes from narrative text, summarise "
        "dated sentences and word the title, scope and questions; all findings come from deterministic checks.",
    ]


# ── Small helpers ─────────────────────────────────────────────────────────────

def _unique(values) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _clean(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clean_list(values: list[str]) -> list[str]:
    return [v for v in (_clean(v, 300) for v in values) if v and not ACCUSATORY.search(v)][:_MAX_LIST]
