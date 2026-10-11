"""
services/reconciliation/communications.py
-----------------------------------------
Communications adapter — runs when the case contains calls, chat messages or emails.

  contact_during_action   a person was in contact with a counterparty while acting on
                          that counterparty's document (from the audit log)
  contact_volume          repeated direct contact between a person and an organisation

Closeness in time is reported as a fact to explain, never as proof of intent.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.case_model import Case
from app.services.identities import Entity
from app.services.records import Record
from app.services.reconciliation.base import Observation, day, join_words, person_label, plural
from app.services.reconciliation.procurement import audit_steps

NAME = "Communications"
_CONTACT_WINDOW = timedelta(minutes=30)


@dataclass
class Contact:
    when: datetime
    channel: str          # call | chat message | email
    a: Entity | None
    b: Entity | None
    record: Record


def applies(case: Case) -> bool:
    return any(case.records_of_kind(kind) for kind in ("call", "message", "email"))


def contacts(case: Case) -> list[Contact]:
    """Every timed call, chat message and email between two resolvable parties."""
    found = []
    for record in case.records:
        if record.kind not in {"call", "message", "email"} or not record.when or not record.when.has_time:
            continue
        channel = {"call": "call", "message": "chat message", "email": "email"}[record.kind]
        a, b = record.role("actor"), record.role("counterparty")
        found.append(Contact(record.when.sort_key(case.offset), channel,
                             case.registry.resolve(a), case.registry.resolve(b), record))
    return found


def _contact_during_action(case: Case) -> list[Observation]:
    all_contacts = contacts(case)
    by_person: dict[str, list[tuple]] = {}
    for doc in case.documents.values():
        counterparty = case.registry.resolve(doc.value("vendor"))
        steps = audit_steps(case, doc.doc_id)
        if not counterparty or len(steps) < 2:
            continue
        start, end = case.sort_key(steps[0]), case.sort_key(steps[-1])
        actor = case.registry.resolve(steps[0].role("actor"))
        for contact in all_contacts:
            if actor and _between(contact, actor, counterparty) and start - _CONTACT_WINDOW <= contact.when <= end + _CONTACT_WINDOW:
                by_person.setdefault(actor.key, []).append((doc, contact, steps))

    observations = []
    for key, hits in by_person.items():
        person = case.registry.get(key)
        counterparty = case.registry.resolve(hits[0][0].value("vendor"))
        lines = [
            f"{day(contact.record.when.local)}: {contact.channel} at {contact.record.when.local:%H:%M}, while "
            f"{doc.doc_id} was created ({steps[0].when.local:%H:%M}) and approved ({steps[-1].when.local:%H:%M})"
            for doc, contact, steps in hits
        ]
        records = [r for doc, contact, steps in hits for r in (*doc.records, contact.record, *steps)]
        observations.append(Observation(
            title=f"{person.name} contacted {counterparty.name} while approving its purchase orders",
            detail=f"{person_label(person)} was in contact with {counterparty.name} during the approval of its "
                   "orders — " + "; ".join(lines) + ".",
            severity="high", category="Communications", records=records, subjects=[key, counterparty.key],
            basis="pattern", rule="communications.contact_during_action",
            why="Direct contact with a supplier while approving its orders may indicate a close relationship "
                "that needs explaining; it may also be routine order clarification.",
            question=f"What was discussed between {person.name} and {counterparty.name} around these approvals?",
            verify=["Interview the people involved about the purpose of these contacts.",
                    "Check whether any declaration of interest is on file."],
        ))
    return observations


def _contact_volume(case: Case) -> list[Observation]:
    pairs: dict[tuple[str, str], list[Contact]] = {}
    for contact in contacts(case):
        people = [e for e in (contact.a, contact.b) if e and e.kind == "person" and not e.organisation]
        orgs = [e for e in (contact.a, contact.b) if e and e.kind == "organisation"]
        for person in people:
            for org in orgs:
                pairs.setdefault((person.key, org.key), []).append(contact)

    observations = []
    for (person_key, org_key), items in pairs.items():
        if len(items) < 3:
            continue
        person, org = case.registry.get(person_key), case.registry.get(org_key)
        channels = Counter(item.channel for item in items)
        summary = join_words([plural(count, channel) for channel, count in channels.most_common()])
        first, last = min(i.when for i in items), max(i.when for i in items)
        others = sorted({p.name for (pk, ok), rows in pairs.items() if ok == org_key and pk != person_key
                         for p in [case.registry.get(pk)]})
        observations.append(Observation(
            title=f"{person.name} was in frequent direct contact with {org.name}",
            detail=f"{person_label(person)} had {summary} with {org.name} between {day(first)} and {day(last)}.",
            severity="medium" if len(items) >= 5 else "low",
            category="Communications", records=[i.record for i in items], subjects=[person_key, org_key],
            basis="documented", rule="communications.contact_volume",
            why="Frequent direct contact is normal for some roles; it matters when the same person also controls "
                "approvals or payments for that organisation.",
            question=f"Is regular contact with {org.name} part of {person.name}'s role?",
            verify=["Confirm the person's responsibilities for this organisation."],
            qualifiers=[f"Other staff in contact with {org.name}: {join_words(others)}."] if others else [],
        ))
    return observations


RULES = (_contact_during_action, _contact_volume)


def _between(contact: Contact, person: Entity, org: Entity) -> bool:
    parties = {contact.a.key if contact.a else "", contact.b.key if contact.b else ""}
    return person.key in parties and org.key in parties
