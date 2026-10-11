"""
agents/subject_analyzer.py
----------------------------
Agent 06 — Subject Analyzer.

Builds a factual profile of every person and user account that matters to the
case, from what the records show they did — documents requested / approved /
created, actions in activity logs, calls and messages, statements given — and
the validated findings that name them.

Each subject gets:
  role               designation, "Contact at <organisation>" or "User account"
  category           Employee | Vendor contact | User account | Witness | Investigation team
  involvement        High | Medium | Low (blank for witnesses and the investigation team)
  involvement_basis  why, in plain words (see INVOLVEMENT_CRITERIA)
  note               what the evidence shows them doing (presence and actions)
  context            evidence that may explain or limit the concern
  identity_note      aliases matched only by partial name / initials / email domain

Presence is not involvement: appearing in many files only adds to the note.
"""

from __future__ import annotations

import re
from collections import Counter

from app.agents.base import AgentResult, AgentRun, PipelineContext
from app.services.case_model import Case
from app.services.identities import Entity
from app.services.records import compact, kind_name

INVOLVEMENT_CRITERIA = {
    "High": "Requires further review — named in at least one validated high-severity finding.",
    "Medium": "Connected to findings — named in validated medium- or low-severity findings only.",
    "Low": "Appears in the evidence or acted in the records, but no finding names them.",
}
_INVESTIGATOR_TITLE = re.compile(r"audit|compliance|investigat", re.IGNORECASE)
_TITLE_AFTER_NAME = r",\s*((?:[A-Z][A-Za-z&-]*\s){0,4}(?:Officer|Auditor|Manager|Executive|Head|Director|Engineer|Analyst))"
_INVOLVEMENT_ORDER = {"High": 0, "Medium": 1, "Low": 2, "": 3}
_CATEGORY_ORDER = {"Employee": 0, "Vendor contact": 1, "User account": 2, "Witness": 3, "Investigation team": 4}
# Fields naming who did what to the record they belong to.
_ACTION_FIELDS = {"requestedby": "requested", "approvedby": "approved", "createdby": "created",
                  "preparedby": "prepared", "reviewedby": "reviewed", "submittedby": "submitted"}
_SIGN_IN = re.compile(r"log\s?-?(?:in|on)|sign\s?-?in", re.IGNORECASE)


async def run(ctx: PipelineContext, agent: AgentRun) -> AgentResult:
    case: Case = ctx.case
    findings = [f for f in ctx.outputs.get("validator", {}).get("findings", []) if f.get("source") == "rule"]
    mentions = case.mentions_by_entity()

    persons = []
    for subject in case.registry.subjects():
        refs = mentions.get(subject.key, set())
        named_in = [f for f in findings if subject.key in f.get("subjects", [])]
        actions, statement = _activity(case, subject)
        if not (named_in or actions or statement or len(refs) >= 2):
            continue
        persons.append(_profile(case, subject, refs, named_in, actions, statement))

    persons.sort(key=lambda p: (_INVOLVEMENT_ORDER[p["involvement"]], _CATEGORY_ORDER[p["category"]], -len(p["refs"])))
    for person in persons[:8]:
        await agent.log("ok", f"{person['name']} — {person['category']}"
                              + (f", {person['involvement']} involvement" if person["involvement"] else ""))
    uncertain = [p for p in persons if p["identity_note"]]
    if uncertain:
        await agent.log("info", f"{len(uncertain)} subject(s) have aliases that need identity confirmation.")
    high = sum(p["involvement"] == "High" for p in persons)
    summary = [f"{len(persons)} people profiled", f"{high} require further review"]
    counts = {"profiled": len(persons), "high": high, "uncertain_identity": len(uncertain)}
    return AgentResult({"persons": persons, "criteria": INVOLVEMENT_CRITERIA}, summary, "done", counts=counts)


def _profile(case: Case, subject: Entity, refs: set[str], named_in: list[dict], actions: Counter, statement: bool) -> dict:
    title = subject.title or _title_from_text(case, subject)
    if subject.organisation:
        category, role = "Vendor contact", f"Contact at {subject.organisation}"
    elif subject.kind == "account":
        category, role = "User account", "User account (owner not identified)"
    elif title and _INVESTIGATOR_TITLE.search(title) and not named_in:
        category, role = "Investigation team", title
    elif statement and not (named_in or actions):
        category, role = "Witness", title or "Witness"
    else:
        category, role = "Employee", title

    ids = ", ".join(f["id"] for f in named_in)
    if category in {"Investigation team", "Witness"}:
        involvement, basis = "", "Provided information to the investigation."
    elif any(f["severity"] == "high" for f in named_in):
        involvement = "High"
        basis = f"Requires further review — named in high-severity finding{'s' if len(named_in) > 1 else ''} {ids}."
    elif named_in:
        involvement, basis = "Medium", f"Connected to finding{'s' if len(named_in) > 1 else ''} {ids}."
    else:
        involvement, basis = "Low", "Appears in the evidence; no finding names them."

    sentences = []
    if actions:
        sentences.append(_capitalise("; ".join(_describe(actions))) + ".")
    if statement:
        sentences.append("Gave a witness statement.")
    sentences.append(f"Appears in {len(refs)} of {len(case.evidence)} evidence files.")

    aliases = sorted(case.registry.uncertain_aliases.get(subject.key, ()))
    return {
        "key": subject.key,
        "name": subject.name,
        "role": role,
        "category": category,
        "involvement": involvement,
        "involvement_basis": basis,
        "note": " ".join(sentences),
        "context": list(dict.fromkeys(q for f in named_in for q in f.get("qualifiers", []))),
        "identity_note": (f"Also referred to as {', '.join(repr(a) for a in aliases[:3])} — matched by partial name, "
                          "initials or email domain; confirm these refer to the same party.") if aliases else "",
        "refs": sorted(refs | {ref for f in named_in for ref in f.get("refs", [])}),
        "red_flags": [f["id"] for f in named_in],
    }


def _activity(case: Case, subject: Entity) -> tuple[Counter, bool]:
    """Count what the records show this subject doing, keyed by (kind of action, detail, extra)."""
    def is_me(value: str) -> bool:
        return bool(value) and case.registry.resolve(value, record_uncertain=False) is subject

    actions: Counter = Counter()
    statement = False
    seen: set[tuple[str, str]] = set()
    for record in case.records:
        kind = record.kind
        if record.primary_id:
            for key, value in record.fields.items():
                verb = _ACTION_FIELDS.get(compact(key))
                if verb and is_me(value) and (verb, record.primary_id) not in seen:
                    seen.add((verb, record.primary_id))
                    actions[("document", verb, kind)] += 1
        if kind == "audit_event" and is_me(record.role("actor")):
            action = " ".join(re.sub(r"[_\-]+", " ", record.role("action")).lower().split()).replace(" po", " PO")
            actions[("system", "sign-in" if _SIGN_IN.search(action) else action, "")] += 1
        elif kind == "call" and is_me(record.role("actor")):
            actions[("call", case.name(record.role("counterparty")), "")] += 1
        elif kind in {"message", "email"}:
            channel = "chat message" if kind == "message" else "email"
            if is_me(record.role("actor")):
                actions[("sent", channel, "")] += 1
            elif is_me(record.role("counterparty")):
                actions[("received", channel, "")] += 1
        elif kind == "vendor_event" and is_me(record.value("updatedbyemployeeid")):
            actions[("vendor", record.value("eventtype").lower(), case.name(record.value("vendorid")))] += 1
        elif kind == "statement" and is_me(record.value("witness")):
            statement = True
    return actions, statement


def _describe(actions: Counter) -> list[str]:
    """Render activity counts as short phrases, most significant first."""
    def plural(n: int, word: str) -> str:
        return f"{n} {word}" if n == 1 else f"{n} {word}s"

    def of(kind: str) -> list[tuple[str, str, int]]:
        return [(a, b, n) for (k, a, b), n in actions.items() if k == kind]

    phrases = [f"{verb} {n} {kind_name(record_kind, n)}" for verb, record_kind, n in of("document")]
    if of("system"):
        total = sum(n for _, _, n in of("system"))
        breakdown = ", ".join(f"{action} ×{n}" for action, _, n in of("system"))
        phrases.append(f"{total} activity log {'entry' if total == 1 else 'entries'} ({breakdown})")
    for event_type in dict.fromkeys(a for a, _, _ in of("vendor")):
        vendors = [v for a, v, _ in of("vendor") if a == event_type]
        target = vendors[0] if len(vendors) == 1 else f"{len(vendors)} vendors ({', '.join(vendors)})"
        phrases.append(f"recorded {event_type} for {target}")
    phrases += [f"made {plural(n, 'call')} to {receiver}" for receiver, _, n in of("call")]
    for direction in ("sent", "received"):
        parts = [plural(n, channel) for channel, _, n in of(direction)]
        if parts:
            phrases.append(f"{direction} " + " and ".join(parts))
    return phrases


def _title_from_text(case: Case, subject: Entity) -> str:
    pattern = re.compile(re.escape(subject.name) + _TITLE_AFTER_NAME)
    for item in case.evidence:
        match = pattern.search(item.text)
        if match:
            return match.group(1).strip()
    return ""


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:]
