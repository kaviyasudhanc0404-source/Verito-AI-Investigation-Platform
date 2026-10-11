"""
agents/timeline_builder.py
----------------------------
Agent 03 — Timeline Builder.

Builds one chronological, plain-language timeline:
  • every dated structured record becomes an event — well-known record types get a
    tailored sentence ("Purchase order PO-1 raised for …"), any other record is
    described from its fields' roles ("j.doe: signed in — vpn-gw-01 (FAILURE) from 203.0.113.7")
  • the same document seen in several files becomes one event citing all of them
  • dated sentences in narrative documents become events, summarised by the LLM
    (or kept verbatim if the LLM is unavailable)
  • reference-data dates far outside the period of activity (e.g. a registration
    years earlier) are left out, and the number left out is reported

Every event keeps its date type (created / approved / settled / event …), the
original timestamp text, its timezone (or "not stated") and whether the date is
approximate. Ordering normalises to UTC when zones are stated; dates and times
always come from the same record or sentence — never borrowed from elsewhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import BaseModel

from app.agents.base import AgentResult, AgentRun, PipelineContext
from app.services.case_model import ROLES, Case
from app.services.llm_service import chat_completion_json, failure_reason
from app.services.records import Record, compact, field_role, kind_name
from app.services.validation import ACCUSATORY
from app.services.values import Timestamp, find_dates, find_ids, id_type, parse_amount, parse_timestamp, prose_sentences

_SYSTEM_PROMPT = """You turn dated sentences from evidence documents into timeline entries.

For each candidate return {"id": "<id>", "keep": true|false, "event": "<what happened on that date, max 25 words>"}.
Set keep=false for date ranges, future deadlines, or dates that only label the document itself.
Use only facts in the sentence. Never state guilt or intent. Return JSON: {"events": [...]}"""

_LLM_BATCH = 30
CATEGORY_WEIGHT = {
    "Payment": 3, "Purchase order": 3, "Vendor": 3, "Approval": 3, "Invoice": 2, "Access": 2,
    "System activity": 2, "Call": 2, "Record": 1, "Chat": 1, "Email": 1, "Statement": 1, "Document": 1,
}
_CONTEXT_CATEGORIES = {"Vendor", "Statement"}      # reference data: frames the period, does not define it
_SIGN_IN = re.compile(r"^(?:log\s?-?in|logon|sign\s?-?in)$", re.IGNORECASE)
_SIGN_OUT = re.compile(r"^(?:log\s?-?out|logoff|sign\s?-?out)$", re.IGNORECASE)
_ROUTINE_OUTCOME = re.compile(r"^(?:success(?:ful)?|completed|ok|done)$", re.IGNORECASE)
_FAILED_OUTCOME = re.compile(r"fail|denied|invalid|reject|locked", re.IGNORECASE)
_IRREGULAR = {"send": "sent", "read": "read", "write": "wrote", "run": "ran", "set": "set", "get": "got", "pay": "paid"}


class _Decision(BaseModel):
    id: str
    keep: bool = True
    event: str = ""


class _Reply(BaseModel):
    events: list[_Decision] = []


@dataclass
class Event:
    stamp: Timestamp
    text: str
    category: str
    refs: set[str] = field(default_factory=set)
    key: tuple = ()
    date_type: str = "event"
    records: list[str] = field(default_factory=list)

    def to_dict(self, offset: timedelta | None) -> dict:
        return {
            "when": self.stamp.sort_key(offset).isoformat(),
            "date": self.stamp.day_label,
            "time": self.stamp.local.strftime("%H:%M") if self.stamp.has_time else "",
            "zone": self.stamp.zone if self.stamp.has_time else "",
            "approximate": self.stamp.approximate,
            "original": self.stamp.raw,
            "date_type": self.date_type,
            "event": self.text,
            "category": self.category,
            "refs": sorted(self.refs),
            "records": self.records[:4],
        }


async def run(ctx: PipelineContext, agent: AgentRun) -> AgentResult:
    case: Case = ctx.case
    record_events = _merge([event for record in case.records for event in _record_events(case, record)])
    await agent.log("ok", f"{len(record_events)} event(s) built from structured records.")

    candidates = _prose_candidates(case, record_events)
    prose_events = await _summarise(candidates, agent) if candidates else []
    await agent.log("ok", f"{len(prose_events)} event(s) taken from narrative documents.")

    kept, left_out = _within_window(case, record_events, prose_events)
    events = sorted(kept, key=lambda e: (e.stamp.sort_key(case.offset), e.text))
    if left_out:
        await agent.log("info", f"{left_out} reference date(s) far outside the period of activity were left out.")
    unzoned = sum(e.stamp.has_time and e.stamp.offset is None for e in events)
    zoned = sum(e.stamp.offset is not None for e in events)
    if unzoned and zoned:
        await agent.log("warn", f"{unzoned} timed event(s) state no timezone; they are assumed to use the case's main "
                                "timezone when ordered.")
    counts = {"events": len(events), "record_events": len(record_events), "document_events": len(prose_events),
              "left_out": left_out, "approximate": sum(e.stamp.approximate for e in events), "unzoned": unzoned}
    if not events:
        await agent.log("warn", "No dated events were found in the evidence.")
        return AgentResult({"events": [], "window": "", "counts": counts}, ["No dated events"], agent.status, counts=counts)

    exact = [e for e in events if not e.stamp.approximate] or events       # "March 2026" does not set the period
    window = f"{exact[0].stamp.local:%d %b %Y} – {exact[-1].stamp.local:%d %b %Y}"
    await agent.log("ok", f"Timeline complete: {len(events)} events, {window}.")
    output = {"events": [e.to_dict(case.offset) for e in events], "window": window, "counts": counts}
    return AgentResult(output, [f"{len(events)} events sequenced", f"Window: {window}"], agent.status, counts=counts)


# ── Record events ─────────────────────────────────────────────────────────────

def _record_events(case: Case, r: Record) -> list[Event]:
    """(date field, text, category, key) for one record → Events, using the record's own dates only."""
    name, money = case.name, case.money
    rid = r.primary_id
    events: list[tuple[str, str, str, tuple]] = []      # ("" = the record's main date)

    if r.kind == "purchase_order":
        vendor, amount = name(_party(r)), parse_amount(r.value(*ROLES["amount"]))
        detail = r.value("description")
        people = f"Requested by {name(r.value('requestedby'))}; approved by {name(r.value('approvedby'))}." \
            if r.value("requestedby") else ""
        text = f"Purchase order {rid} raised for {vendor} — {money(amount)}" + (f" ({detail})" if detail else "") + f". {people}"
        events.append(("", text.strip(), "Purchase order", ("po", rid)))
        if r.value("approvaldate") and _day(r.value("approvaldate")) != _day(r.value("date", "podate")):
            events.append(("approvaldate", f"{rid} approved by {name(r.value('approvedby'))}.", "Approval", ("po-ok", rid)))
    elif r.kind == "invoice":
        vendor, amount = name(_party(r)), parse_amount(r.value(*ROLES["amount"]))
        po = r.value(*ROLES["po"])
        events.append(("", f"{vendor} issued invoice {rid}" + (f" against {po}" if po else "") + f" — {money(amount)}.",
                       "Invoice", ("inv", rid)))
        paid_key = next((k for k in ("paiddate", "paidon") if r.value(k)), "")
        if paid_key:
            events.append((paid_key, f"Invoice {rid} recorded as paid.", "Payment", ("inv-paid", rid)))
    elif r.kind == "payment":
        amount, invoice = parse_amount(r.value(*ROLES["amount"])), r.value(*ROLES["invoice"])
        events.append(("", f"Bank payment {rid} of {money(amount)} to {name(_party(r))}"
                       + (f" for invoice {invoice}." if invoice else "."), "Payment", ("txn", rid)))
    elif r.kind == "call":
        duration = r.value("duration")
        events.append(("", f"{name(r.role('actor'))} called {name(r.role('counterparty'))}"
                       + (f" ({duration})." if duration else "."), "Call", ("call", rid or r.text())))
    elif r.kind in {"message", "email"}:
        sender, recipient = name(r.role("actor")), name(r.role("counterparty"))
        if r.kind == "message":
            text, category = f"Chat from {sender} to {recipient}: “{_clip(r.value('message'), 140)}”", "Chat"
        else:
            text, category = f"Email from {sender} to {recipient}: “{_clip(r.value('subject'), 120)}”", "Email"
        events.append(("", text, category, (r.kind, r.evidence_id, r.index)))
    elif r.kind == "vendor_event":
        actor = r.value("updatedbyemployeeid")
        events.append(("", f"{name(r.value('vendorid'))}: {r.value('eventtype').lower()}"
                       + (f" (recorded by {name(actor)})." if actor else "."), "Vendor",
                       ("vendor", r.value("vendorid"), _day(r.value("eventdate")))))
    elif r.kind == "bank_detail":
        updated, effective = r.value("lastupdated"), r.value("effectivedate")
        if updated and _day(updated) != _day(effective):
            events.append(("lastupdated", f"Bank details of {name(r.value('vendorid', 'vendorname'))} updated.", "Vendor",
                           ("vendor", r.value("vendorid"), _day(updated))))
    elif r.kind == "vendor":
        for key, label in (("registrationdate", "registered"), ("empanelmentdate", "empanelment")):
            if parse_timestamp(r.value(key)):
                events.append((key, f"{r.value('vendorname')}: vendor {label}.", "Vendor",
                               ("vendor", r.value("vendorid"), _day(r.value(key)))))
    elif r.kind == "statement" and r.value("witness"):
        events.append(("", f"Witness statement given by {name(r.value('witness'))}.",
                       "Statement", ("ws", r.value("witness"), _day(r.value("statementdate")))))
    elif r.kind == "audit_event":
        events.append(("", _activity_text(case, r), _activity_category(r), ("act", rid or r.ref)))
    elif r.kind == "generic":
        events.append(("", _generic_text(r), "Record", ("rec", rid or r.ref)))

    dates = {compact(key): (dtype, stamp) for key, dtype, stamp in r.dates()}
    built = []
    for date_key, text, category, key in events:
        dtype, stamp = dates.get(date_key) or _main(r)
        if stamp:
            built.append(Event(stamp, text, category, {r.evidence_id}, key, dtype, [r.citation]))
    return built


def _main(r: Record) -> tuple[str, Timestamp | None]:
    stamp = r.when
    dtype = next((dtype for _, dtype, s in r.dates() if s == stamp), "event")
    return dtype, stamp


def _activity_text(case: Case, r: Record) -> str:
    """'Arvind Kumaraswamy created PO-2026-0342 in Procurement Portal.' from any activity-log row."""
    actor = case.name(r.role("actor")) or "Unknown user"
    targets = sorted((v for k, v in r.fields.items() if field_role(compact(k)) == "target" and id_type(v) != "session"),
                     key=lambda v: not find_ids(v))          # the record acted on first, then the system
    target = targets[0] if targets else ""
    outcome, ip = r.role("outcome"), r.role("ip")
    sign_in = _SIGN_IN.match(_words(r.role("action")))
    if sign_in and _FAILED_OUTCOME.search(outcome):
        text, outcome = f"{actor} failed to sign in to {target}", ""
    else:
        text = f"{actor} {_past_tense(r.role('action'), target)}"
    if len(targets) > 1:
        text += f" in {targets[1]}"
    if outcome and not _ROUTINE_OUTCOME.match(outcome):
        text += f" ({outcome})"
    if ip and sign_in:
        text += f" from {ip}"
    return f"{' '.join(text.split())}."


def _activity_category(r: Record) -> str:
    action = _words(r.role("action"))
    return "Access" if _SIGN_IN.match(action) or _SIGN_OUT.match(action) else "System activity"


def _past_tense(action: str, target: str) -> str:
    """'CREATE_PO' + 'PO-1' → 'created PO-1'; 'RELEASE_PAYMENT' + 'INV-1' → 'released payment for INV-1'."""
    words = _words(action).split()
    if not words:
        return f"acted on {target}".strip()
    if _SIGN_IN.match(" ".join(words)):
        return f"signed in to {target}".strip() if target else "signed in"
    if _SIGN_OUT.match(" ".join(words)):
        return f"signed out of {target}".strip() if target else "signed out"
    verb, rest = words[0], [w.upper() if len(w) <= 3 else w for w in words[1:]]
    past = _IRREGULAR.get(verb) or (verb + "d" if verb.endswith("e") else verb[:-1] + "ied"
                                    if re.search(r"[^aeiou]y$", verb) else verb + "ed")
    noun = " ".join(rest)
    if noun and target and target.upper().startswith(noun.upper().replace(" ", "")):
        noun = ""                                       # 'created PO PO-1' → 'created PO-1'
    joiner = " for " if noun and target and id_type(target) != noun.lower() else " "
    return " ".join(f"{past} {noun}{joiner if target else ''}{target}".split())


def _generic_text(r: Record) -> str:
    """'INC-2026-0042 (Incidents, row 3): Severity: High; Status: Open.' from any other dated record."""
    shown = [(k, v) for k, v in r.fields.items() if v != r.primary_id and not parse_timestamp(v)][:3]
    label = r.primary_id or f"{kind_name(r.kind).capitalize()} in {r.location}"
    return f"{label}: " + "; ".join(f"{k}: {_clip(v, 60)}" for k, v in shown) + "."


def _merge(events: list[Event]) -> list[Event]:
    """Collapse the same event seen in several files into one with all citations."""
    merged: dict[tuple, Event] = {}
    for event in events:
        key = (event.key, event.stamp.local.date())
        if key in merged:
            existing = merged[key]
            existing.refs |= event.refs
            existing.records += event.records
            if event.stamp.has_time and not existing.stamp.has_time:
                existing.stamp = event.stamp
        else:
            merged[key] = event
    return list(merged.values())


# ── Narrative events ──────────────────────────────────────────────────────────

def _prose_candidates(case: Case, record_events: list[Event]) -> list[dict]:
    known = {(e.stamp.local.date(), ref) for e in record_events for ref in find_ids(e.text)}
    candidates, seen = [], set()
    for item in case.evidence:
        for sentence in prose_sentences(item.text):
            dates = find_dates(sentence)
            if not dates or len(sentence) < 25:
                continue
            first = dates[0]
            ids = find_ids(sentence)
            if ids and all((first.when.date(), ref) in known for ref in ids):
                continue                       # already covered by a record event
            signature = (first.when.date(), sentence[:80].casefold())
            if signature in seen:
                continue
            seen.add(signature)
            candidates.append({"id": f"N-{len(candidates) + 1:03d}", "stamp": first.timestamp,
                               "text": _clip(sentence, 300), "evidence_id": item.evidence_id, "file": item.filename})
    return candidates


async def _summarise(candidates: list[dict], agent: AgentRun) -> list[Event]:
    decisions: dict[str, dict] = {}
    for start in range(0, len(candidates), _LLM_BATCH):
        batch = candidates[start:start + _LLM_BATCH]
        payload = [{"id": c["id"], "date": c["stamp"].display(), "file": c["file"], "text": c["text"]} for c in batch]
        try:
            reply = await chat_completion_json(
                [{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                max_tokens=60 * len(batch) + 200, purpose="timeline.summarise", schema=_Reply,
            )
            decisions.update({d["id"]: d for d in reply["events"]})
        except Exception as exc:
            await agent.fallback(f"AI summary of {len(batch)} document date(s) unavailable ({failure_reason(exc)}); "
                                 "using the original sentences.", units=len(batch))

    events = []
    for c in candidates:
        decision = decisions.get(c["id"], {})
        if decision.get("keep") is False:
            continue
        summary = " ".join(str(decision.get("event") or "").split())
        text = summary if summary and not ACCUSATORY.search(summary) else c["text"]
        events.append(Event(c["stamp"], text, "Document", {c["evidence_id"]}, ("doc", c["id"]),
                            records=[f"{c['evidence_id']} · {c['file']}"]))
    return events


def _within_window(case: Case, record_events: list[Event], prose_events: list[Event]) -> tuple[list[Event], int]:
    """Keep events within 60 days before / 30 days after the period of activity. Returns (kept, left out)."""
    everything = record_events + prose_events
    core = [e.stamp.sort_key(case.offset) for e in record_events if e.category not in _CONTEXT_CATEGORIES]
    if not core:
        return everything, 0
    start, end = min(core) - timedelta(days=60), max(core) + timedelta(days=30)
    kept = [e for e in everything if start <= e.stamp.sort_key(case.offset) <= end]
    return kept, len(everything) - len(kept)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _party(r: Record) -> str:
    return r.value(*ROLES["vendor"]) or r.role("counterparty")


def _words(action: str) -> str:
    return " ".join(re.sub(r"[_\-]+", " ", action or "").lower().split())


def _day(value: str):
    stamp = parse_timestamp(value)
    return stamp.local.date() if stamp else None


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
