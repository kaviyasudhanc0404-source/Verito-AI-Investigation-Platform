"""
services/document_facts.py
--------------------------
Statements from narrative documents worth reporting alongside the rule-based
observations: sentences that report something missing, an instruction, or a
first-hand account. Deterministic, so every statement is quoted verbatim.

    select_document_facts(case, analyzer_output, observations) → [{"id": "D-01", "text", "evidence_id"}]

Candidates are only verbatim text: the quotes the analyzer picked (already checked
to appear in their chunk) plus every prose sentence with an investigative signal.
The analyzer's paraphrased findings are never presented as quotes. Candidates are
ranked, de-duplicated, capped per file, and statements that merely repeat an
observation are dropped.
"""

from __future__ import annotations

import re
from collections import Counter

from app.services.case_model import Case
from app.services.values import find_ids, prose_sentences

# Sentences that report something missing, an instruction, or a first-hand account.
SIGNAL = re.compile(
    r"\b(not (?:located|found|available|on file|verified)|(?:was|were) not|no [\w-]+(?: [\w-]+){0,6} (?:was|were) "
    r"(?:located|found)|without|missing|on priority|expedit\w*|directly|instruct\w*|asked|requests? that|"
    r"declaration of interest|conflict of interest|relationship|referred|recall\w*|"
    r"did not (?:receive|verify|know|pursue))\b", re.IGNORECASE)
# Sentences about the document itself or disclaimers, which are not facts about the case.
_META = re.compile(
    r"^(the|this)\s+(exhibit|document|memorandum|file|report|investigation)\b|\blists\b|"
    r"\bdo(?:es)? not (?:establish|determine|constitute)\b|\bno (?:conclusion|determination|finding)\b",
    re.IGNORECASE)
# "Transaction ID: TXN-1" / "Submitted By: …" — a field label, not a statement.
_LABEL_LINE = re.compile(r"^[A-Z][\w ./()&-]{0,40}:\s")
_MAX_FACTS, _MAX_PER_FILE = 20, 3


def select_document_facts(case: Case, analyzer: dict, observations: list[dict]) -> list[dict]:
    candidates = list(analyzer.get("statements", []))
    for item in case.evidence:
        candidates += [{"text": s, "evidence_id": item.evidence_id} for s in prose_sentences(item.text) if SIGNAL.search(s)]
    ranked = _rank(candidates, observations)
    return [{"id": f"D-{number:02d}", **fact} for number, fact in enumerate(ranked, start=1)]


def _rank(facts: list[dict], observations: list[dict]) -> list[dict]:
    """Order facts by investigative value; drop duplicates, meta text and repeats of observations."""
    flagged = {i for o in observations for i in find_ids(o["detail"])}
    scored, seen = [], set()
    for index, fact in enumerate(facts):
        text = " ".join(str(fact.get("text", "")).split()).strip("'\"“”‘’ ")
        key = re.sub(r"\W+", " ", text.casefold()).strip()
        if not 40 <= len(text) <= 320 or key in seen or _LABEL_LINE.match(text) or _META.search(text)                 or _repeats_observation(text, observations):
            continue
        seen.add(key)
        ids, signal = set(find_ids(text)), bool(SIGNAL.search(text))
        score = 3 * signal + bool(ids) - 2 * (len(ids & flagged) >= 2 and not signal)
        scored.append((score, index, {"text": text, "evidence_id": fact["evidence_id"]}))
    chosen, per_file = [], Counter()
    for score, _, fact in sorted(scored, key=lambda entry: (-entry[0], entry[1])):
        if score > 0 and per_file[fact["evidence_id"]] < _MAX_PER_FILE:
            chosen.append(fact)
            per_file[fact["evidence_id"]] += 1
    return chosen[:_MAX_FACTS]


def _repeats_observation(text: str, observations: list[dict]) -> bool:
    """True when most of the text's key words already appear in one observation."""
    words = _stems(text)
    return bool(words) and any(
        len(words & _stems(f"{o['title']} {o['detail']}")) / len(words) >= 0.6 for o in observations
    )


def _stems(text: str) -> set[str]:
    return {word[:5] for word in re.findall(r"[a-z]{4,}", text.lower())}
