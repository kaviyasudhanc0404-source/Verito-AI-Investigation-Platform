"""
agents/evidence_linker.py
---------------------------
Agent 04 — Evidence Linker.

1. Connections: which evidence files describe the same things. Each link states
   its type, how it was found, what the files share and a supporting excerpt
   from each file:
     duplicate copy       the files hold the same records (an export of the same data);
                          they corroborate nothing and count as one source
     conflicting records  the files hold the same documents with different values
     shared identifiers   the same document IDs, IP addresses, hosts or accounts
     shared parties       the same people or organisations only (weaker)
2. Red flags: runs the reconciliation rules (services/reconciliation) — generic
   cross-document checks plus the domain adapters that apply to this case.

Everything here is deterministic — links and red flags are facts that can be
checked against the cited files, so no LLM is involved.
"""

from __future__ import annotations

import itertools
from collections import defaultdict

from app.agents.base import AgentResult, AgentRun, PipelineContext
from app.services.case_model import Case
from app.services.reconciliation import adapters_used, find_observations
from app.services.values import find_ids, find_ips, id_type

_MAX_LINKS = 15
_WEIGHTS = {"identifier": 3.0, "person": 1.5, "organisation": 1.0, "account": 1.5}
# Identifiers that are just context (cost centres, policies, sessions) don't link files.
_CONTEXT_ID_TYPES = {"cost centre", "policy", "session"}


async def run(ctx: PipelineContext, agent: AgentRun) -> AgentResult:
    case: Case = ctx.case
    adapters = adapters_used(case)
    await agent.log("info", "Checks applied: generic consistency checks"
                            + (f" + {', '.join(adapters)}" if adapters else "") + ".")
    observations = find_observations(case)
    ctx.observations = observations
    links = _links(case, observations)
    await agent.log("ok", f"{len(links)} connection(s) found between evidence files.")
    for link in links[:5]:
        await agent.log("info", f"{link['a']} ↔ {link['b']} ({link['type']}): {link['reason']}")

    for observation in observations:
        await agent.log("warn" if observation.severity == "high" else "info",
                        f"Red flag ({observation.severity}): {observation.title}")
    if not observations:
        await agent.log("ok", "No cross-evidence red flags detected.")

    high = sum(o.severity == "high" for o in observations)
    output = {"links": links, "observations": [o.to_dict() for o in observations], "adapters": adapters}
    summary = [f"{len(links)} connections", f"{len(observations)} red flags ({high} high)"]
    counts = {"links": len(links), "observations": len(observations), "copies": len(case.copies)}
    return AgentResult(output, summary, "done", counts=counts)


def _links(case: Case, observations: list) -> list[dict]:
    shared: dict[str, dict[str, set[str]]] = {}
    for item in case.evidence:
        ids = {i for i in find_ids(item.text) if id_type(i) not in _CONTEXT_ID_TYPES} | set(find_ips(item.text))
        groups: dict[str, set[str]] = {"identifier": ids, "person": set(), "organisation": set(), "account": set()}
        for key in case.registry.mentions(item.text):
            entity = case.registry.get(key)
            groups[entity.kind].add(entity.name)
            ids -= entity.ids                      # an employee/vendor ID counts once, as the entity
        shared[item.evidence_id] = groups

    # In larger cases, values present in almost every file (e.g. the main party) say little about a
    # specific pair; in small cases every shared value matters.
    frequency: dict[tuple[str, str], int] = defaultdict(int)
    for values in shared.values():
        for kind, items in values.items():
            for value in items:
                frequency[(kind, value)] += 1
    common_limit = int(len(shared) * 0.75) if len(shared) >= 6 else len(shared) + 1
    conflicts = {tuple(sorted(o.refs)) for o in observations if o.rule == "core.conflicting_values"}

    links = []
    for a, b in itertools.combinations(sorted(shared), 2):
        overlap = {kind: sorted(v for v in shared[a][kind] & shared[b][kind] if frequency[(kind, v)] < common_limit)
                   for kind in _WEIGHTS}
        score = sum(_WEIGHTS[kind] * min(len(values), 4) for kind, values in overlap.items())
        copy = next((n for x, y, n in case.copies if {x, y} == {a, b}), 0)
        if not copy and not overlap["identifier"] and score < 3:
            continue
        link_type, method, strength = _classify(a, b, overlap, score, copy, conflicts)
        reason = (f"Both files hold the same {copy} records, so they are one source, not two."
                  if copy else _reason(overlap))
        links.append({
            "a": a, "b": b, "a_name": case.filename(a), "b_name": case.filename(b),
            "type": link_type, "method": method, "reason": reason, "strength": strength,
            "score": 0 if copy else score, "shared": overlap,
            "excerpts": _excerpts(case, a, b, overlap),
        })
    return sorted(links, key=lambda link: (-link["score"], link["type"] != "duplicate copy"))[:_MAX_LINKS]


def _classify(a: str, b: str, overlap: dict, score: float, copy: int, conflicts: set) -> tuple[str, str, str]:
    if copy:
        return "duplicate copy", "identical records in both files", "copy"
    if any(set(refs) >= {a, b} for refs in conflicts):
        return "conflicting records", "same document IDs with different values", "strong"
    strength = "strong" if score >= 9 else "moderate" if score >= 4.5 else "weak"
    if overlap["identifier"]:
        return "shared identifiers", "exact identifier match", strength
    return "shared parties", "name and alias matching", strength


def _reason(overlap: dict[str, list[str]]) -> str:
    parts = []
    by_type: dict[str, list[str]] = defaultdict(list)
    for identifier in overlap["identifier"]:
        by_type[id_type(identifier)].append(identifier)
    for kind, ids in sorted(by_type.items(), key=lambda item: -len(item[1])):
        listed = ", ".join(ids[:2]) + (f" and {len(ids) - 2} more" if len(ids) > 2 else "")
        parts.append(f"{kind}{'es' if kind.endswith('ss') and len(ids) > 1 else 's' if len(ids) > 1 else ''} {listed}")
    names = overlap["organisation"][:2] + overlap["person"][:2] + overlap["account"][:2]
    if names:
        parts.append(", ".join(names))
    return "Both refer to " + "; ".join(parts) + "." if parts else "Related content."


def _excerpts(case: Case, a: str, b: str, overlap: dict) -> list[dict]:
    """One line from each file showing the first shared value."""
    token = next((values[0] for kind in ("identifier", "account", "organisation", "person")
                  if (values := overlap[kind])), "")
    excerpts = []
    for ref in (a, b):
        line = next((line.strip() for line in case.text_of(ref).split("\n") if token and token in line), "")
        if line:
            excerpts.append({"ref": ref, "text": line if len(line) <= 220 else line[:219] + "…"})
    return excerpts
