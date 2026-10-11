"""
agents/finding_validator.py
-----------------------------
Agent 05 — Finding Validator.

An independent check between analysis and reporting:
  1. Gathers candidate findings: the reconciliation observations (Evidence Linker)
     and the strongest quoted statements from narrative documents.
  2. Re-checks each one against the evidence (services/validation.py): citations
     exist, quotes are verbatim, identifiers / amounts / dates appear in the cited
     sources, calculations recompute, wording is neutral. Failures are withheld
     with their reasons; duplicates are merged.
  3. Assigns each accepted finding a claim type, confidence and confidence basis.
  4. Applies the configurable quality gate (QUALITY_GATE_POLICY = warn | partial | block).
     "block" stops the report; "partial" marks it as partial with the reasons.
"""

from __future__ import annotations

from app.agents.base import AgentResult, AgentRun, PipelineContext
from app.services.case_model import Case
from app.services.document_facts import select_document_facts
from app.services.validation import quality_gate, validate_findings


async def run(ctx: PipelineContext, agent: AgentRun) -> AgentResult:
    case: Case = ctx.case
    analyzer, collector = ctx.outputs.get("analyzer", {}), ctx.outputs.get("collector", {})
    observations = ctx.observations
    facts = select_document_facts(case, analyzer, [o.to_dict() for o in observations])
    await agent.log("scan", f"Checking {len(observations)} red flag(s) and {len(facts)} document statement(s) "
                            "against the evidence...")

    findings, rejected = validate_findings(case, observations, facts)
    for item in rejected:
        await agent.log("warn", f"Withheld {item['id']} “{item['title'][:80]}”: {'; '.join(item['reasons'])}.")
    adjusted = [f for f in findings if f["validation"]["status"] == "adjusted"]
    for finding in adjusted:
        await agent.log("info", f"{finding['id']}: {'; '.join(finding['validation']['notes'])}.")
    await agent.log("ok", f"{len(findings)} finding(s) passed validation, {len(adjusted)} adjusted, "
                          f"{len(rejected)} withheld.")

    chunk_counts = analyzer.get("chunks", {})
    gate = quality_gate(
        files_total=len(collector.get("files", {})) or len(case.evidence),
        files_failed=len(collector.get("failed_ids", [])),
        narrative_chunks=chunk_counts.get("narrative", 0),
        fallback_chunks=chunk_counts.get("fallback", 0),
        rejected=len(rejected),
        findings=len(findings),
        events=len(ctx.outputs.get("timeline", {}).get("events", [])),
    )
    if gate["status"] == "passed":
        await agent.log("ok", "Quality gate passed.")
    else:
        for reason in gate["reasons"]:
            await agent.log("warn", f"Quality gate ({gate['policy']}): {reason}")

    output = {"findings": findings, "rejected": rejected, "quality": gate}
    counts = {"checked": len(observations) + len(facts), "passed": len(findings) - len(adjusted),
              "adjusted": len(adjusted), "rejected": len(rejected)}
    summary = [f"{len(findings)} findings validated"] + ([f"{len(rejected)} withheld"] if rejected else []) \
        + ([f"Quality: {gate['status']}"] if gate["status"] != "passed" else [])
    if gate["status"] == "blocked":
        return AgentResult(output, summary, "blocked", "The quality gate blocked the report: " + " ".join(gate["reasons"]),
                           counts)
    return AgentResult(output, summary, agent.status, counts=counts)
