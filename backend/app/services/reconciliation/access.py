"""
services/reconciliation/access.py
---------------------------------
Access & activity-log adapter — runs when the case contains activity-log records
with an account, an action and a time (sign-in logs, audit trails, VPN or
application logs).

  failed_then_success     several failed sign-ins followed by a successful one
  new_source              an account active from an address or host not seen before for it
  activity_after_alert    sensitive actions on an account after a security alert about it
  unusual_hours           activity at hours when the case's logs are otherwise quiet

Each pattern has ordinary explanations (forgotten passwords, travel, shift work),
so they are reported for review, never as conclusions.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta

from app.services.case_model import Case
from app.services.records import Record
from app.services.reconciliation.base import Observation, day, plural

NAME = "Access and activity logs"
_FAILED = re.compile(r"fail|denied|invalid|reject|locked|error|unauthori[sz]ed|bad password", re.IGNORECASE)
_SUCCESS = re.compile(r"success|succeeded|ok\b|allowed|granted|accepted|passed", re.IGNORECASE)
_SIGN_IN = re.compile(r"log\s?-?in|logon|sign\s?-?in|auth", re.IGNORECASE)
_SENSITIVE = re.compile(r"download|export|copy|delete|remove|transfer|upload|privilege|grant|disable|"
                        r"reset|modify|change|approve|release", re.IGNORECASE)
_ALERT = re.compile(r"alert|incident|suspicious|unusual|compromis|breach|phishing|malware|anomal", re.IGNORECASE)
_FAILURE_RUN = 3
_FAILURE_WINDOW = timedelta(hours=1)
_ALERT_WINDOW = timedelta(hours=48)
_MIN_EVENTS_FOR_HOURS = 10


def applies(case: Case) -> bool:
    return bool(_activity(case))


def _activity(case: Case) -> list[Record]:
    return [r for r in case.records if r.kind == "audit_event" and r.when and r.when.has_time]


def _account_key(case: Case, record: Record) -> str:
    """Registry key of the record's actor, or the raw actor value when it is not registered."""
    actor = record.role("actor")
    entity = case.registry.resolve(actor)
    return entity.key if entity else actor


def _by_account(case: Case) -> dict[str, list[Record]]:
    grouped: dict[str, list[Record]] = {}
    for record in _activity(case):
        grouped.setdefault(_account_key(case, record), []).append(record)
    for records in grouped.values():
        records.sort(key=case.sort_key)
    return grouped


def _account_name(case: Case, key: str) -> str:
    entity = case.registry.get(key)
    return entity.name if entity else key


def _outcome(record: Record) -> str:
    return f"{record.role('outcome')} {record.role('action')}"


def _failed_then_success(case: Case) -> list[Observation]:
    observations = []
    for key, records in _by_account(case).items():
        failures: list[Record] = []
        for record in records:
            outcome = _outcome(record)
            if _FAILED.search(outcome):
                failures = [f for f in failures if case.sort_key(record) - case.sort_key(f) <= _FAILURE_WINDOW] + [record]
            elif _SUCCESS.search(outcome) and len(failures) >= _FAILURE_RUN:
                name = _account_name(case, key)
                sources = sorted({r.role("ip") or r.role("target") for r in failures + [record]} - {""})
                observations.append(Observation(
                    title=f"{plural(len(failures), 'failed sign-in')} on {name} were followed by a successful one",
                    detail=(f"The logs show {plural(len(failures), 'failed attempt')} for {name} between "
                            f"{failures[0].when.display()} and {failures[-1].when.display()}, then a successful "
                            f"{record.role('action').lower() or 'attempt'} at {record.when.display()}"
                            + (f" (from {', '.join(sources)})." if sources else ".")),
                    severity="medium", category="Account access", records=[*failures, record], subjects=[key],
                    basis="documented", rule="access.failed_then_success",
                    why="Repeated failures followed by success can indicate password guessing — or simply a user "
                        "who mistyped or reset a password.",
                    question=f"Did the owner of {name} make these attempts themselves?",
                    verify=["Confirm with the account owner whether they made the attempts.",
                            "Check whether the source address belongs to the organisation or a known device."],
                ))
                failures = []
    return observations


def _new_source(case: Case) -> list[Observation]:
    observations = []
    for key, records in _by_account(case).items():
        seen: set[str] = set()
        baseline = 0
        for record in records:
            source = record.role("ip")
            if not source:
                continue
            if seen and source not in seen and baseline >= 3:
                name = _account_name(case, key)
                later = [r for r in records if r.role("ip") == source]
                observations.append(Observation(
                    title=f"{name} was used from a new address ({source})",
                    detail=(f"{name} was active from {', '.join(sorted(seen))} in {plural(baseline, 'earlier event')}, "
                            f"then from {source} starting {record.when.display()} "
                            f"({plural(len(later), 'event')} from that address)."),
                    severity="low", category="Account access", records=later[:10], subjects=[key], basis="pattern",
                    rule="access.new_source",
                    why="A new address can mean remote work or a new device, but also use of the account by "
                        "someone else.",
                    question=f"Is {source} a known address or device for {name}?",
                    verify=["Look up the address in network and VPN records."],
                ))
                break
            seen.add(source)
            baseline += 1
    return observations


def _activity_after_alert(case: Case) -> list[Observation]:
    alerts = [r for r in case.records if r.when and r.kind != "audit_event"
              and _ALERT.search(" ".join(filter(None, (r.role("description"), r.role("action"), r.body[:300]))))]
    observations = []
    for key, records in _by_account(case).items():
        for alert in alerts:
            text = f"{alert.text()} {alert.body}"
            mentioned = key in case.registry.mentions(text) if case.registry.get(key) else key.lower() in text.lower()
            if not mentioned:
                continue
            start = case.sort_key(alert)
            after = [r for r in records if start < case.sort_key(r) <= start + _ALERT_WINDOW
                     and _SENSITIVE.search(r.role("action"))]
            if not after:
                continue
            name = _account_name(case, key)
            actions = Counter(r.role("action").lower() for r in after)
            observations.append(Observation(
                title=f"Sensitive activity on {name} continued after a security alert",
                detail=(f"An alert about {name} was sent at {alert.when.display()}; within the next 48 hours the logs "
                        f"show {', '.join(f'{a} ×{n}' for a, n in actions.most_common())} on that account, the first "
                        f"at {after[0].when.display()}."),
                severity="medium", category="Account access", records=[alert, *after[:10]], subjects=[key],
                basis="documented", rule="access.activity_after_alert",
                why="Activity after an alert shows whether the account was contained in time.",
                question=f"Who performed the actions on {name} after the alert, and was the account restricted?",
                verify=["Check when the account was locked or its password reset.",
                        "Confirm with the account owner whether they performed these actions."],
            ))
            break
    return observations


def _unusual_hours(case: Case) -> list[Observation]:
    events = _activity(case)
    if len(events) < _MIN_EVENTS_FOR_HOURS:
        return []
    hours = Counter(r.when.local.hour for r in events)
    quiet = {h for h in range(24) if hours[h] <= 0.05 * len(events) and (h < 6 or h >= 22)}
    odd = [r for r in events if r.when.local.hour in quiet]
    if not odd or len(odd) > 0.1 * len(events):
        return []
    by_account = Counter(_account_name(case, _account_key(case, r)) for r in odd)
    return [Observation(
        title=f"{plural(len(odd), 'logged action')} took place at night",
        detail=(f"Most logged activity happens during the day; {plural(len(odd), 'action')} took place between "
                f"22:00 and 06:00 ({', '.join(f'{n}: {c}' for n, c in by_account.most_common())}), first on "
                f"{day(min(r.when.local for r in odd))}."),
        severity="low", category="Account access", records=odd[:10], basis="pattern", rule="access.unusual_hours",
        why="Night-time activity can be shift work or scheduled jobs; it is worth checking alongside other signals.",
        question="Were these accounts expected to be active at night?",
        verify=["Compare with shift rosters, travel and scheduled jobs."],
    )]


RULES = (_failed_then_success, _new_source, _activity_after_alert, _unusual_hours)
