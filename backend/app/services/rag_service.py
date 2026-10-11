"""
services/rag_service.py
------------------------
Answer follow-up questions about an investigation.

question → semantic search over the case's evidence chunks (Qdrant Cloud)
         → LLM answer grounded in those excerpts, the report's key facts and the
           recent conversation → cited evidence IDs extracted from the answer
"""

import asyncio
import re

from app.services import vector_store
from app.services.llm_service import chat_completion

_SYSTEM_PROMPT = """You are VERITO, an investigation assistant. Answer using only the evidence excerpts and case facts provided.

Rules:
- Cite evidence by ID in square brackets, e.g. [E-003] or [E-003, E-007].
- If the evidence does not answer the question, say: "The available evidence does not provide enough information to answer this question."
- Never invent facts or citations, and never state that anyone is guilty.
- Answer in plain, concise English that a non-specialist can follow."""

_CITATION = re.compile(r"\bE-\d{3,}\b")


async def answer_question(
    question: str,
    investigation_id: str,
    user_id: str,
    chat_history: list[dict],
    report: dict | None = None,
) -> tuple[str, list[str]]:
    """Return (answer, cited evidence IDs)."""
    chunks = await asyncio.to_thread(
        vector_store.query_evidence, query=question, investigation_id=investigation_id, user_id=user_id, n_results=6,
    )
    excerpts = "\n\n".join(f"[{c['evidence_id']}] {c['filename']}\n{c['text']}" for c in chunks) \
        or "(No relevant evidence excerpts found.)"

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if report:
        messages.append({"role": "system", "content": "Case facts from the final report:\n" + _report_facts(report)})
    messages += [
        {"role": "assistant" if m["role"] == "verito" else "user", "content": m["content"]}
        for m in chat_history
    ]
    messages.append({"role": "user", "content": f"EVIDENCE EXCERPTS:\n{excerpts}\n\nQUESTION: {question}"})

    answer = await chat_completion(messages, temperature=0.2, max_tokens=1024, purpose="chat.answer")
    return answer, sorted(set(_CITATION.findall(answer)))


def _report_facts(report: dict) -> str:
    lines = [report.get("summary", "")]
    for finding in report.get("findings", [])[:8]:
        if isinstance(finding, dict):
            lines.append(f"- {finding.get('title')}: {finding.get('detail', '')} [{', '.join(finding.get('refs', []))}]")
        else:
            lines.append(f"- {finding}")
    return "\n".join(line for line in lines if line)
