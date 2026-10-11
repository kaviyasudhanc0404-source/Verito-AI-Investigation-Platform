"""
services/reconciliation/procurement.py
--------------------------------------
Procurement & payments adapter — runs only when the case contains purchase
orders, invoices or payments.

  approval_threshold        orders clustered just below a stated approval limit
  self_approval             the same person requested and approved an order
  new_vendor                vendor set up / bank details changed just before its first order
  payment_speed             one vendor paid much faster than the others
  payment_before_documents  payment released before the invoice or order date
  payment_date_mismatch     bank payment date differs from the recorded paid date
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from statistics import mean

from app.services.case_model import Case, Document
from app.services.identities import Entity
from app.services.records import Record
from app.services.reconciliation.base import Observation, calculation, day, join_words, person_label, plural
from app.services.values import find_amounts, parse_timestamp

NAME = "Procurement and payments"
_THRESHOLD_CONTEXT = re.compile(r"threshold|exceed|above|approver|approval|sanction", re.IGNORECASE)


def applies(case: Case) -> bool:
    return any(case.of_kind(kind) for kind in ("purchase_order", "invoice", "payment"))


def approval_threshold(case: Case) -> tuple[float, list[str]] | None:
    """The most frequently stated approval limit and the files that state it."""
    amounts: Counter[float] = Counter()
    sources: dict[float, set[str]] = {}
    for item in case.evidence:
        for sentence in re.split(r"(?<=[.!?])\s+|\n", item.text):
            if _THRESHOLD_CONTEXT.search(sentence):
                for amount in find_amounts(sentence):
                    if amount >= 1000:
                        amounts[amount] += 1
                        sources.setdefault(amount, set()).add(item.evidence_id)
    if not amounts:
        return None
    limit = amounts.most_common(1)[0][0]
    return limit, sorted(sources[limit])


def _approval_threshold(case: Case) -> list[Observation]:
    found = approval_threshold(case)
    if not found:
        return []
    limit, policy_refs = found
    by_vendor = _orders_by_vendor(case)
    near_by_vendor = {v: [po for po in orders if po.amount() is not None and 0.9 * limit <= po.amount() < limit]
                      for v, orders in by_vendor.items()}
    observations = []
    for vendor, near in near_by_vendor.items():
        if len(near) < 2:
            continue
        amounts = [po.amount() for po in near]
        others = sum(len(items) for other, items in near_by_vendor.items() if other is not vendor)
        observations.append(Observation(
            title=f"{len(near)} purchase orders to {vendor.name} were just under the {case.money(limit)} approval limit",
            detail=(
                f"{_join_ids(near)} were each valued between {case.money(min(amounts))} and "
                f"{case.money(max(amounts))} — below the {case.money(limit)} limit above which a second "
                f"approver is required. Together they total {case.money(sum(amounts))}."
            ),
            severity="high" if len(near) >= 3 else "medium",
            category="Approval controls",
            records=_records(near), extra_refs=policy_refs, subjects=[vendor.key], basis="pattern",
            rule="procurement.approval_threshold",
            why="Keeping each order just under the limit means no second person reviewed this spending.",
            question="Were these orders separate requirements, or one requirement split into several orders?",
            verify=["Compare the item descriptions, sites and dates of the flagged orders.",
                    "Ask the requester and approver why the requirement was ordered in parts."],
            qualifiers=[f"{plural(others, 'order')} to other vendors also fell within 10% below the limit."] if others else
                       ["No orders to other vendors fell within 10% below the limit."],
            calculation=calculation("sum", amounts, sum(amounts), "Total value of the orders just under the limit", "amount"),
        ))
    return observations


def _self_approval(case: Case) -> list[Observation]:
    by_person: dict[str, list[Document]] = {}
    for po in case.of_kind("purchase_order"):
        requester = case.registry.resolve(po.value("requested_by"))
        approver = case.registry.resolve(po.value("approved_by"))
        if requester and requester is approver:
            by_person.setdefault(requester.key, []).append(po)

    observations = []
    for key, orders in by_person.items():
        person = case.registry.get(key)
        gaps, ips, audit_records = _create_approve_gaps(case, person, orders)
        detail = f"{person_label(person)} is recorded as both the requester and the approver of {_join_ids(orders)}."
        if gaps:
            detail += (
                f" The system audit log shows each approval {min(gaps)}–{max(gaps)} minutes after the order was created"
                + (f", by the same user from the same IP address ({', '.join(sorted(ips))})." if len(ips) == 1 else ".")
            )
        others = sorted({case.name(po.value("approved_by")) for po in case.of_kind("purchase_order")
                         if po not in orders and po.value("approved_by")})
        observations.append(Observation(
            title=f"{person.name} requested and approved {plural(len(orders), 'purchase order')} alone",
            detail=detail,
            severity="high",
            category="Segregation of duties",
            records=_records(orders) + audit_records, subjects=[person.key], rule="procurement.self_approval",
            why="When one person both requests and approves an order, the check against mistakes or misuse is lost.",
            question="How was one person able to both request and approve the same purchase orders?",
            verify=["Check the system access rights that allowed the same user to request and approve.",
                    "Confirm whether a delegation of authority was in place for these orders."],
            qualifiers=[f"Other purchase orders in the same records were approved by {join_words(others)}."] if others else [],
            calculation=calculation("range", gaps, [min(gaps), max(gaps)],
                                    "Minutes between creation and approval in the audit log", "minutes") if gaps else None,
        ))
    return observations


def _new_vendor(case: Case) -> list[Observation]:
    observations = []
    events = case.records_of_kind("vendor_event")
    for vendor, orders in _orders_by_vendor(case).items():
        first_order = min((po.date() for po in orders if po.date()), default=None)
        if not first_order:
            continue
        own_events = [e for e in events if case.registry.resolve(e.value("vendorid")) is vendor and e.when]
        empanelled = _event_date(own_events, r"empanel|registr|onboard") or _vendor_date(case, vendor, "empanelmentdate", "registrationdate")
        bank_change = _event_date(own_events, r"bank") or _bank_update(case, vendor)
        recent_empanel = empanelled and timedelta(0) <= first_order - empanelled[0] <= timedelta(days=60)
        recent_bank = bank_change and timedelta(0) <= first_order - bank_change[0] <= timedelta(days=30)
        if not (recent_empanel or recent_bank):
            continue

        parts, records = [], _records(orders)
        if empanelled:
            parts.append(f"{vendor.name} was empanelled on {day(empanelled[0])}{_by(case, empanelled[1])}")
            records.append(empanelled[1])
        if bank_change:
            gap = (first_order - bank_change[0]).days
            parts.append(f"its bank details were changed on {day(bank_change[0])}{_by(case, bank_change[1])} — "
                         f"{plural(gap, 'day')} before the first purchase order ({day(first_order)})")
            records.append(bank_change[1])
        detail = "; ".join(parts) + "."
        approvers = _other_empanelment_actors(case, events, vendor, empanelled)
        if approvers:
            detail += f" All other vendor empanelments in the records were recorded by {join_words(approvers)}."
        observations.append(Observation(
            title=f"{vendor.name} was set up shortly before receiving orders",
            detail=detail,
            severity="high" if recent_bank else "medium",
            category="Vendor onboarding",
            records=records,
            subjects=[vendor.key, *_actor_keys(case, [e[1] for e in (empanelled, bank_change) if e])],
            basis="documented", rule="procurement.new_vendor",
            why="A supplier whose bank account changes just before its first order is a known warning sign "
                "for payment diversion, although new suppliers are often onboarded for urgent work.",
            question="Who requested the vendor's empanelment and bank-detail change, and what documents supported them?",
            verify=["Verify the vendor's registration documents and ownership.",
                    "Obtain the bank-change request and confirm how it was authorised."],
            calculation=calculation("difference_days", [bank_change[0].isoformat(), first_order.isoformat()],
                                    (first_order - bank_change[0]).days,
                                    "Days from the bank-detail change to the first order", "days") if bank_change else None,
        ))
    return observations


def _payment_speed(case: Case) -> list[Observation]:
    days_by_vendor: dict[str, list[tuple[Document, int]]] = {}
    for invoice in case.of_kind("invoice"):
        issued, paid = invoice.date("date"), invoice.date("paid_date")
        vendor = case.registry.resolve(invoice.value("vendor"))
        if issued and paid and vendor and paid >= issued:
            days_by_vendor.setdefault(vendor.key, []).append((invoice, (paid - issued).days))

    observations = []
    for key, items in days_by_vendor.items():
        others = [days for other, rows in days_by_vendor.items() if other != key for _, days in rows]
        own = [days for _, days in items]
        if len(own) < 2 or not others or mean(own) > 3 or mean(others) < 2 * max(mean(own), 1):
            continue
        vendor = case.registry.get(key)
        invoices = [i for i, _ in items]
        observations.append(Observation(
            title=f"Invoices from {vendor.name} were paid unusually fast",
            detail=(f"{len(own)} invoices from {vendor.name} ({_join_ids(invoices)}) were paid {_avg(own)} days "
                    f"after the invoice date on average, compared with {_avg(others)} days for the other vendors "
                    "in the same records."),
            severity="medium", category="Payments", records=_records(invoices), subjects=[key], basis="pattern",
            rule="procurement.payment_speed",
            why="Payments that skip the normal cycle bypass the checks that confirm goods were delivered.",
            question="Why were these invoices paid outside the normal payment cycle?",
            verify=["Ask Finance who requested early payment and whether goods receipt was confirmed first."],
            calculation=calculation("mean", own, round(mean(own), 1), "Average days from invoice to payment for this vendor", "days"),
        ))
    return observations


def _payment_before_documents(case: Case) -> list[Observation]:
    observations = []
    for event in case.records_of_kind("audit_event"):
        action = event.role("action").upper()
        invoice = case.documents.get(event.value("recordid"))
        if not (re.search(r"PAY|RELEASE", action) and invoice and event.when):
            continue
        released = event.when.local
        order = case.documents.get(invoice.value("po"))
        issued, ordered = invoice.date("date"), order.date() if order else None
        problems = []
        if issued and released.date() < issued.date():
            problems.append(f"the invoice is dated {day(issued)}")
        if ordered and released.date() < ordered.date():
            problems.append(f"its purchase order {order.doc_id} is dated {day(ordered)}")
        if not problems:
            continue
        actor = case.registry.resolve(event.role("actor"))
        observations.append(Observation(
            title=f"Payment for {invoice.doc_id} was released before the invoice date",
            detail=(f"The system audit log shows {person_label(actor)} releasing payment for {invoice.doc_id} on "
                    f"{day(released, True)}, but {join_words(problems)}."),
            severity="high", category="Payments",
            records=[event, *invoice.records, *(order.records if order else [])],
            subjects=[actor.key] if actor else [], rule="procurement.payment_before_documents",
            why="A payment released before the invoice exists skips the check that the amount is owed.",
            question=f"On what basis was payment for {invoice.doc_id} released before the invoice date?",
            verify=["Compare the audit log entry with the bank statement for the same payment.",
                    "Check whether the invoice date was entered or changed after the payment."],
        ))
    return observations


def _payment_date_mismatch(case: Case) -> list[Observation]:
    mismatches = []
    for payment in case.of_kind("payment"):
        invoice = case.documents.get(payment.value("invoice"))
        paid_bank, paid_recorded = payment.date("date"), invoice.date("paid_date") if invoice else None
        if paid_bank and paid_recorded and abs((paid_bank - paid_recorded).days) >= 2:
            mismatches.append((invoice, payment, paid_recorded, paid_bank))
    if not mismatches:
        return []
    lines = [f"{inv.doc_id}: recorded as paid on {day(recorded)}, but bank payment {pay.doc_id} is dated {day(bank)}"
             for inv, pay, recorded, bank in mismatches]
    return [Observation(
        title="Payment dates do not match between the invoice records and the bank records",
        detail="; ".join(lines) + ".",
        severity="medium", category="Record consistency",
        records=[r for inv, pay, _, _ in mismatches for r in inv.records + pay.records],
        rule="procurement.payment_date_mismatch",
        why="When records disagree, at least one of them is wrong, so the true sequence must be confirmed.",
        question="Which payment dates are correct, and why do the records disagree?",
        verify=["Obtain bank statements to confirm the actual payment dates."],
    )]


RULES = (_approval_threshold, _self_approval, _new_vendor, _payment_speed, _payment_before_documents,
         _payment_date_mismatch)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _orders_by_vendor(case: Case) -> dict[Entity, list[Document]]:
    grouped: dict[Entity, list[Document]] = {}
    for po in case.of_kind("purchase_order"):
        vendor = case.registry.resolve(po.value("vendor"))
        if vendor:
            grouped.setdefault(vendor, []).append(po)
    for orders in grouped.values():
        orders.sort(key=lambda po: po.date() or datetime.max)
    return grouped


def audit_steps(case: Case, record_id: str) -> list[Record]:
    """Audit-log entries about one document, in time order."""
    steps = [r for r in case.records_of_kind("audit_event") if r.value("recordid") == record_id and r.when]
    return sorted(steps, key=case.sort_key)


def _create_approve_gaps(case: Case, person: Entity, orders: list[Document]) -> tuple[list[int], set[str], list[Record]]:
    gaps, ips, records = [], set(), []
    for po in orders:
        steps = [s for s in audit_steps(case, po.doc_id) if case.registry.resolve(s.role("actor")) is person]
        created = next((s for s in steps if "CREATE" in s.role("action").upper()), None)
        approved = next((s for s in steps if "APPROV" in s.role("action").upper()), None)
        if created and approved:
            gaps.append(round((approved.when.local - created.when.local).total_seconds() / 60))
            ips.update(filter(None, (created.role("ip"), approved.role("ip"))))
            records += [created, approved]
    return gaps, ips, records


def _event_date(events: list[Record], pattern: str) -> tuple[datetime, Record] | None:
    matches = [e for e in events if re.search(pattern, e.value("eventtype"), re.IGNORECASE)]
    return (matches[-1].when.local, matches[-1]) if matches else None


def _vendor_date(case: Case, vendor: Entity, *keys: str) -> tuple[datetime, Record] | None:
    for record in case.records_of_kind("vendor"):
        if case.registry.resolve(record.value("vendorid")) is vendor:
            stamp = parse_timestamp(record.value(*keys))
            if stamp:
                return stamp.local, record
    return None


def _bank_update(case: Case, vendor: Entity) -> tuple[datetime, Record] | None:
    for record in case.records_of_kind("bank_detail"):
        if case.registry.resolve(record.value("vendorid", "vendorname")) is not vendor:
            continue
        updated, effective = parse_timestamp(record.value("lastupdated")), parse_timestamp(record.value("effectivedate"))
        if updated and (not effective or updated.local > effective.local):
            return updated.local, record
    return None


def _other_empanelment_actors(case: Case, events: list[Record], vendor: Entity, own: tuple | None) -> list[str]:
    if not own or not own[1].value("updatedbyemployeeid"):
        return []
    own_actor = case.name(own[1].value("updatedbyemployeeid"))
    others = {
        case.name(e.value("updatedbyemployeeid")) for e in events
        if re.search(r"empanel|registr|onboard", e.value("eventtype"), re.IGNORECASE)
        and case.registry.resolve(e.value("vendorid")) is not vendor and e.value("updatedbyemployeeid")
    }
    return sorted(others) if others and own_actor not in others else []


def _actor_keys(case: Case, records: list[Record]) -> list[str]:
    actors = (case.registry.resolve(r.value("updatedbyemployeeid", "updatedby")) for r in records)
    return list(dict.fromkeys(a.key for a in actors if a))


def _by(case: Case, record: Record) -> str:
    actor = record.value("updatedbyemployeeid", "updatedby")
    return f" (recorded by {case.name(actor)})" if actor else ""


def _records(documents: list[Document]) -> list[Record]:
    return [r for doc in documents for r in doc.records]


def _avg(values: list[int]) -> str:
    return f"{mean(values):.1f}".rstrip("0").rstrip(".")


def _join_ids(documents: list[Document]) -> str:
    return join_words([doc.doc_id for doc in documents])
