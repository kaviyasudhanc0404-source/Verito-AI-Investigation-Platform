"""
services/reconciliation/core.py
-------------------------------
Generic cross-document checks that run on every case, whatever its domain.

  conflicting_values   the same document ID holds different amounts, dates, parties
                       or statuses in different sources
  amount_mismatch      a record's amount differs from the document it refers to
  duplicate_entries    the same ID entered twice in one source, or different IDs with
                       identical amount, counterparty and date
  chronology           a later lifecycle stage dated before an earlier one
                       (approved before created, settled before executed …), or a
                       record dated before the document it refers to
  missing_counterparts a record refers to a document of a type the evidence contains,
                       but that document itself is not in the evidence

Domain adapters run first. Each rule receives the record sets of the adapter
observations (`covered`) and skips any item those records already explain.
"""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict

from app.services.case_model import Case, Document
from app.services.records import LIFECYCLE, Record, compact, field_role, is_date_field, kind_name
from app.services.reconciliation.base import Observation, day, join_words, plural
from app.services.values import find_ids, id_shape, id_type, parse_amount, parse_timestamp

_COMPARED_ROLES = {"amount", "counterparty", "actor", "outcome"}
_SEVERITY_BY_FIELD = {"amount": "high", "date": "medium", "counterparty": "medium", "actor": "medium", "outcome": "low"}
_MAX_LINES = 8


# ── Conflicting values ────────────────────────────────────────────────────────

def _conflicting_values(case: Case, covered: list[set[str]]) -> list[Observation]:
    by_kind: dict[str, list[tuple[Document, str, list[Record], str]]] = defaultdict(list)
    for doc in case.documents.values():
        if len({r.evidence_id for r in doc.records}) < 2:
            continue
        for key, kind in _compared_fields(doc):
            values: dict[str, list[Record]] = defaultdict(list)
            for record in doc.records:
                raw = record.compact_fields.get(key, "")
                if raw:
                    values[_normal(case, raw, kind)].append(record)
            if len(values) > 1 and len({r.evidence_id for rs in values.values() for r in rs}) > 1:
                records = [rs[0] for rs in values.values()]
                if _explained(records, covered):
                    continue
                by_kind[doc.kind].append((doc, _label(records[0], key), records, kind))

    observations = []
    for kind, conflicts in by_kind.items():
        docs = list(dict.fromkeys(doc.doc_id for doc, *_ in conflicts))
        lines = [f"{doc.doc_id} — {label}: " + " vs ".join(
                     f"{r.compact_fields[compact(label)]} in {r.citation}" for r in records)
                 for doc, label, records, _ in conflicts[:_MAX_LINES]]
        severity = min((_SEVERITY_BY_FIELD[k] for *_, k in conflicts), key=["high", "medium", "low"].index)
        observations.append(Observation(
            title=f"Sources disagree about {_count(len(docs), kind)}",
            detail="; ".join(lines) + (f"; and {len(conflicts) - _MAX_LINES} more." if len(conflicts) > _MAX_LINES else "."),
            severity=severity, category="Record consistency",
            records=[r for _, _, records, _ in conflicts for r in records], rule="core.conflicting_values",
            why="When two sources disagree about the same document, at least one is wrong or was changed.",
            question="Which source holds the correct values, and why do the others differ?",
            verify=["Obtain the original system record for each listed document.",
                    "Check the change history of the fields that differ."],
        ))
    return observations


def _compared_fields(doc: Document) -> list[tuple[str, str]]:
    keys: dict[str, str] = {}
    for record in doc.records:
        for key in record.compact_fields:
            role = field_role(key)
            if is_date_field(key):
                keys.setdefault(key, "date")
            elif role in _COMPARED_ROLES:
                keys.setdefault(key, role)
    return list(keys.items())


def _normal(case: Case, value: str, kind: str) -> str:
    if kind == "amount":
        amount = parse_amount(value)
        return f"{amount:.2f}" if amount is not None else value.strip().lower()
    if kind == "date":
        stamp = parse_timestamp(value)
        return stamp.local.date().isoformat() if stamp else value.strip().lower()
    if kind in {"counterparty", "actor"}:
        entity = case.registry.resolve(value, record_uncertain=False)
        return entity.key if entity else " ".join(value.lower().split())
    return " ".join(value.lower().split())


def _label(record: Record, key: str) -> str:
    return next((name for name in record.fields if compact(name) == key), key)


def _amount_mismatch(case: Case, covered: list[set[str]]) -> list[Observation]:
    """A record whose amount differs from the amount of the document it refers to (e.g. payment vs invoice)."""
    items = []
    for record in case.records:
        amount = parse_amount(record.role("amount"))
        if amount is None:
            continue
        targets = {value.strip() for value in record.compact_fields.values()} - {record.primary_id}
        for target in filter(None, map(case.documents.get, sorted(targets))):
            expected = None if case.is_party(target.doc_id) else target.amount()
            if expected and abs(amount - expected) > max(1.0, 0.01 * expected) \
                    and not _explained([record, *target.records], covered):
                items.append((record, target, amount, expected))
    if not items:
        return []
    lines = [f"{r.primary_id or r.citation} records {case.money(a)} against {t.doc_id}, which is for {case.money(e)} "
             f"({(a - e) / e:+.1%})" for r, t, a, e in items[:_MAX_LINES]]
    return [Observation(
        title=f"{plural(len(items), 'amount')} do not match the document they refer to" if len(items) > 1
              else "An amount does not match the document it refers to",
        detail="; ".join(lines) + ".",
        severity="medium", category="Record consistency",
        records=[x for r, t, _, _ in items for x in (r, *t.records)], rule="core.amount_mismatch",
        why="Amounts that do not reconcile can reflect tax, partial payments or corrections — or an error or overpayment.",
        question="Is each difference explained by tax, a partial payment, a credit note or a correction?",
        verify=["Reconcile each listed amount against the original document and any credit notes."],
    )]


# ── Duplicates ────────────────────────────────────────────────────────────────

def _duplicate_entries(case: Case, covered: list[set[str]]) -> list[Observation]:
    observations = []
    repeated: dict[tuple[str, str, str], list[Record]] = defaultdict(list)
    for record in case.records:
        if record.primary_id:
            repeated[(record.evidence_id, record.source, record.primary_id)].append(record)
    twice = [records for records in repeated.values() if len(records) > 1 and not _explained(records, covered)]
    if twice:
        lines = [f"{rs[0].primary_id} appears {len(rs)} times in {rs[0].evidence_id}"
                 + (f" ({rs[0].source})" if rs[0].source else "") for rs in twice[:_MAX_LINES]]
        observations.append(Observation(
            title=f"{plural(len(twice), 'identifier')} entered more than once in the same source",
            detail="; ".join(lines) + ".",
            severity="medium", category="Duplicates", records=[r for rs in twice for r in rs],
            rule="core.duplicate_entries",
            why="A document entered twice can lead to double processing or payment.",
            question="Were these entries processed more than once?",
            verify=["Check whether each duplicated entry resulted in a separate payment or action."],
        ))

    groups: dict[tuple, list[Document]] = defaultdict(list)
    for doc in case.documents.values():
        amount, party, stamp = doc.amount(), doc.value("vendor"), doc.records[0].when
        if amount and party and stamp:
            entity = case.registry.resolve(party, record_uncertain=False)
            groups[(doc.kind, amount, entity.key if entity else party.lower(), stamp.local.date())].append(doc)
    for (kind, amount, _, when), docs in groups.items():
        if len(docs) < 2 or _explained([r for d in docs for r in d.records], covered):
            continue
        observations.append(Observation(
            title=f"Possible duplicate {kind_name(kind, 2)}: {join_words([d.doc_id for d in docs])}",
            detail=(f"{join_words([d.doc_id for d in docs])} have the same amount ({case.money(amount)}), the same "
                    f"counterparty ({case.name(docs[0].value('vendor'))}) and the same date ({day(when)})."),
            severity="medium", category="Duplicates", records=[r for d in docs for r in d.records], basis="pattern",
            rule="core.possible_duplicates",
            why="Identical documents under different numbers can be a duplicate claim or payment — or recurring "
                "charges of the same size.",
            question="Are these separate transactions or the same one recorded twice?",
            verify=["Compare the underlying supporting documents for each item."],
        ))
    return observations


# ── Chronology ────────────────────────────────────────────────────────────────

def _chronology(case: Case, covered: list[set[str]]) -> list[Observation]:
    issues: dict[tuple[str, str], list[tuple[str, list[Record]]]] = defaultdict(list)

    for doc in case.documents.values():
        stages = [(dtype, stamp, r) for r in doc.records for _, dtype, stamp in r.dates() if dtype in LIFECYCLE]
        for (a_type, a, a_rec), (b_type, b, b_rec) in itertools.combinations(stages, 2):
            if LIFECYCLE[a_type] > LIFECYCLE[b_type]:
                (a_type, a, a_rec), (b_type, b, b_rec) = (b_type, b, b_rec), (a_type, a, a_rec)
            out_of_order = LIFECYCLE[a_type] < LIFECYCLE[b_type] and b.local.date() < a.local.date()
            if out_of_order and not _explained([a_rec, b_rec], covered):
                issues[(doc.kind, "")].append((
                    f"{doc.doc_id} is recorded as {b_type} on {b.display()} but {a_type} on {a.display()}",
                    [a_rec, b_rec]))

    for record in case.records:
        if not record.when:
            continue
        targets = {value.strip() for value in record.compact_fields.values()} - {record.primary_id}
        for target in filter(None, map(case.documents.get, sorted(targets))):
            target_when = next((r.when for r in target.records if r.when), None)
            too_early = target_when and record.when.local.date() < target_when.local.date()
            if too_early and not _explained([record, *target.records], covered):
                label = record.primary_id or record.location
                issues[(record.kind, target.kind)].append((
                    f"{label} ({record.when.display()}) refers to {target.doc_id}, dated {target_when.display()}",
                    [record, *target.records]))

    observations = []
    for (kind, target_kind), items in issues.items():
        money = kind == "payment" or any(_moves_money(r) for _, records in items for r in records[:1])
        if target_kind:
            title = (f"{_count(len(items), kind)} dated before the {kind_name(target_kind, 2)} they refer to"
                     if len(items) > 1 else f"One {kind_name(kind)} is dated before the {kind_name(target_kind)} it refers to")
        else:
            title = f"{_count(len(items), kind)} with dates out of order"
        observations.append(Observation(
            title=title,
            detail="; ".join(line for line, _ in items[:_MAX_LINES]) + ".",
            severity="high" if money else "medium", category="Chronology",
            records=[r for _, records in items for r in records], rule="core.chronology",
            why="Steps recorded in an impossible order suggest a backdated entry, a data-entry error or a "
                "control that was skipped.",
            question="What is the actual order of these steps, and who entered the dates?",
            verify=["Compare the dates with system audit trails or bank statements.",
                    "Check whether any of these dates were edited after entry."],
        ))
    return observations


def _explained(records: list[Record], covered: list[set[str]]) -> bool:
    """True when an adapter observation already rests on all of these records."""
    keys = {r.ref for r in records}
    return any(keys <= known for known in covered)


def _count(count: int, kind: str) -> str:
    return f"{count} {kind_name(kind, count)}"


def _moves_money(record: Record) -> bool:
    action = record.role("action").lower()
    return record.kind == "payment" or any(word in action for word in ("pay", "release", "transfer", "disburse"))


# ── Missing counterparts ──────────────────────────────────────────────────────

def _missing_counterparts(case: Case, covered: list[set[str]]) -> list[Observation]:
    # A reference is "missing" only if documents with the same ID format exist in the evidence —
    # a registration number inside a master record is an attribute, not a missing document.
    shapes = Counter(id_shape(doc_id) for doc_id in case.documents if not case.is_party(doc_id))

    missing: dict[str, dict[str, list[Record]]] = defaultdict(lambda: defaultdict(list))
    for record in case.records:
        for value in record.compact_fields.values():
            ref = value.strip()
            if ref in case.documents or find_ids(ref) != [ref] or shapes[id_shape(ref)] < 2:
                continue
            if not _explained([record], covered):
                missing[id_type(ref).replace("reference", "record")][ref].append(record)

    observations = []
    for ref_type, refs in missing.items():
        mentioned = [ref for ref in refs if any(ref in item.text for item in case.evidence
                                                if item.evidence_id not in {r.evidence_id for r in refs[ref]})]
        observations.append(Observation(
            title=f"{plural(len(refs), ref_type)} referred to in the records {'is' if len(refs) == 1 else 'are'} not in the evidence",
            detail=", ".join(f"{ref} (referred to by {join_words([r.primary_id or r.citation for r in records][:3])})"
                             for ref, records in list(refs.items())[:_MAX_LINES]) + ".",
            severity="low", category="Missing records", records=[r for rs in refs.values() for r in rs],
            rule="core.missing_counterparts",
            why="A reference to a document that cannot be found means part of the trail is missing.",
            question=f"Where are the {ref_type} records for these references?",
            verify=[f"Request the missing {ref_type} records from the system of record."],
            qualifiers=[f"{join_words(mentioned)} {'is' if len(mentioned) == 1 else 'are'} mentioned in other "
                        "documents, so they may exist outside the provided extracts."] if mentioned else [],
        ))
    return observations


RULES = (_conflicting_values, _amount_mismatch, _duplicate_entries, _chronology, _missing_counterparts)
