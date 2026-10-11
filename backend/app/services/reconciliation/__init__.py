"""
services/reconciliation
-----------------------
Rule-based cross-checks over the case model ("red flags").

    observations = find_observations(case)      # numbered F-01, F-02 … most severe first
    adapters_used(case)                         # names of the domain adapters that applied

Rules are grouped into domain adapters that run only when their kind of data is
present, plus generic core checks that run on every case:

  procurement.py      purchase orders, invoices, payments, vendor onboarding
  communications.py   calls, chats and emails around actions
  access.py           sign-in and activity logs
  core.py             conflicting values, duplicates, chronology, missing records

Adapters run first; the core checks skip anything an adapter observation already
explains, so the same issue is never reported twice.
"""

from __future__ import annotations

from app.services.case_model import Case
from app.services.reconciliation import access, communications, core, procurement
from app.services.reconciliation.base import SEVERITY_CRITERIA, SEVERITY_ORDER, Observation

ADAPTERS = (procurement, communications, access)

__all__ = ["ADAPTERS", "SEVERITY_CRITERIA", "Observation", "adapters_used", "find_observations"]


def adapters_used(case: Case) -> list[str]:
    return [adapter.NAME for adapter in ADAPTERS if adapter.applies(case)]


def find_observations(case: Case) -> list[Observation]:
    found = [o for adapter in ADAPTERS if adapter.applies(case) for rule in adapter.RULES for o in rule(case)]
    covered = [o.record_keys for o in found]
    found += [o for rule in core.RULES for o in rule(case, covered)]
    found.sort(key=lambda o: SEVERITY_ORDER.get(o.severity, 3))
    for number, observation in enumerate(found, start=1):
        observation.id = f"F-{number:02d}"
    return found
