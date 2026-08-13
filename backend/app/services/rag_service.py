"""
services/rag_service.py
------------------------
Retrieval-Augmented Generation for follow-up Q&A.

Flow:
  User question
  → Embed question (BAAI/bge-small-en-v1.5 with BGE query instruction)
  → Query Qdrant Cloud (filtered to investigation)
  → Retrieve relevant evidence chunks
  → Build prompt with evidence context
  → Groq LLM generates answer
  → Answer cites evidence IDs
"""

from app.services import vector_store
from app.services.llm_service import chat_completion

# System prompt for the RAG assistant
_SYSTEM_PROMPT = """You are VERITO, an AI forensic investigation assistant.
You answer questions strictly based on the provided evidence excerpts.

Rules:
- Cite evidence by ID in square brackets, e.g. [E-003] or [E-003, E-007].
- If the evidence does not support the answer, say: "The available evidence does not provide enough information to answer this question."
- Never invent evidence, facts, or citations.
- Be concise and precise. Use investigative language.
- Do not speculate beyond what the evidence shows."""


async def answer_question(
    question: str,
    investigation_id: str,
    user_id: str,
    chat_history: list[dict],
) -> tuple[str, list[str]]:
    """
    Answer a user question using RAG over the investigation's evidence.

    Args:
        question: The user's question text.
        investigation_id: The investigation to search within.
        chat_history: Previous messages [{\"role\":..., \"content\":...}].

    Returns:
        (answer_text, cited_evidence_ids)
    """
    # 1. Retrieve relevant evidence chunks from Qdrant Cloud
    chunks = vector_store.query_evidence(
        query=question,
        investigation_id=investigation_id,
        user_id=user_id,
        n_results=6,
    )

    # 2. Build the evidence context block
    if chunks:
        evidence_context = "\n\n".join(
            f"[{c['evidence_id']}] {c['filename']}\n{c['text']}"
            for c in chunks
        )
    else:
        evidence_context = "(No relevant evidence found in the database.)"

    # 3. Build messages for the LLM
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"EVIDENCE CONTEXT:\n{evidence_context}\n\n"
                f"QUESTION: {question}"
            ),
        },
    ]

    # 4. Call Groq
    answer = await chat_completion(messages, temperature=0.2, max_tokens=1024)

    # 5. Extract cited evidence IDs from the answer (e.g. [E-003])
    import re
    cited = list(set(re.findall(r"\[E-\d{3}\]", answer)))
    cited = [c.strip("[]") for c in cited]

    return answer, cited
