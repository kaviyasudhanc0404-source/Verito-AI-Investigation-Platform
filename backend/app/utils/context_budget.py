"""
utils/context_budget.py
-----------------------
Shared token-budget system used by all five investigation agents.

Key concepts (kept separate):
  • Model context window  — total tokens a model can handle (input + output combined)
  • Max completion tokens — maximum output tokens the model may generate
  • Per-request budget    — how many input tokens WE choose to use; configurable,
                            derived from observed endpoint behaviour, NOT from TPM
  • Provider TPM/RPM      — rate limits across all requests per minute/hour;
                            a separate system-level concern, unrelated to single-request size

Usage pattern (every agent):
    budget = get_token_budget(output_reserve=1800)
    compact = fit_items_by_tokens(candidates, budget_tokens=budget.effective_input, ...)
    validation = validate_payload(messages, max_output_tokens=1800, budget=budget)
    if not validation.fits:
        compact = truncate_by_semantic_priority(compact, ...)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings


# ── Public dataclasses ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TokenBudget:
    """Computed token budget for a single LLM call."""
    per_request_max: int       # Configurable per-request ceiling
    system_prompt: int         # Tokens reserved for system prompt
    output_reserve: int        # Tokens reserved for LLM output
    safety_margin: int         # Estimation error buffer
    effective_input: int       # Tokens available for user prompt / serialised context

    def __str__(self) -> str:
        return (
            f"TokenBudget(per_request={self.per_request_max}, "
            f"sys={self.system_prompt}, out={self.output_reserve}, "
            f"safety={self.safety_margin}, effective_input={self.effective_input})"
        )


@dataclass(frozen=True)
class PayloadValidation:
    """Result of pre-call payload validation."""
    fits: bool
    estimated_input_tokens: int
    estimated_total: int        # input + output
    headroom: int               # tokens left after estimated_total vs per_request_max
    reduction_needed: int       # tokens by which input exceeds effective_input (0 if fits)


# ── Token budget factory ───────────────────────────────────────────────────────

def get_token_budget(*, output_reserve: int | None = None) -> TokenBudget:
    """Return a TokenBudget computed from settings.

    Args:
        output_reserve: Override the default output reservation for this call.
                        Use when a specific agent needs more or fewer output tokens.
    """
    s = get_settings()
    out = output_reserve if output_reserve is not None else s.llm_default_output_reserve
    effective = s.llm_per_request_max_tokens - s.llm_system_prompt_reserve - out - s.llm_safety_margin
    effective = max(effective, 1)  # Never negative
    return TokenBudget(
        per_request_max=s.llm_per_request_max_tokens,
        system_prompt=s.llm_system_prompt_reserve,
        output_reserve=out,
        safety_margin=s.llm_safety_margin,
        effective_input=effective,
    )


# ── Token estimation ──────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Estimate token count using multi-factor word-based approach.

    This is an approximation — not a tokenizer. It uses three components:
      • base: ~1.3 tokens per whitespace-delimited word (English text)
      • json_overhead: JSON punctuation ({}[]":,) adds extra tokens
      • digit_overhead: numeric sequences tokenise densely

    Floor: char_count / 5  (absolute minimum)
    Ceiling: char_count / 2  (absolute maximum)

    Character limits remain as secondary safety nets only.
    """
    if not text:
        return 0
    words = text.split()
    word_count = len(words)
    base = int(word_count * 1.3)
    json_chars = sum(1 for c in text if c in '{}[]":,')
    json_overhead = json_chars // 3
    digit_seqs = len(re.findall(r"\d+", text))
    estimate = base + json_overhead + digit_seqs
    char_floor = len(text) // 5
    char_ceiling = len(text) // 2
    return max(char_floor, min(estimate, char_ceiling))


def estimate_tokens_for_messages(messages: list[dict]) -> int:
    """Estimate total input tokens for a messages list."""
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", ""))
        total += 4  # role overhead per message
    total += 2  # conversation envelope
    return total


# ── Payload validation ────────────────────────────────────────────────────────

def validate_payload(
    messages: list[dict],
    *,
    max_output_tokens: int,
    budget: TokenBudget,
) -> PayloadValidation:
    """Check whether messages fit within the per-request token budget.

    The check enforces:
        estimated_input + max_output_tokens <= budget.per_request_max

    This is the constraint that causes 413 errors; it is separate from TPM.

    Returns:
        PayloadValidation with .fits, .estimated_input_tokens,
        .estimated_total, .headroom, .reduction_needed.
    """
    estimated_input = estimate_tokens_for_messages(messages)
    estimated_total = estimated_input + max_output_tokens
    headroom = budget.per_request_max - estimated_total
    fits = estimated_input <= budget.effective_input and estimated_total <= budget.per_request_max
    reduction_needed = max(0, estimated_input - budget.effective_input)
    return PayloadValidation(
        fits=fits,
        estimated_input_tokens=estimated_input,
        estimated_total=estimated_total,
        headroom=headroom,
        reduction_needed=reduction_needed,
    )


# ── Batch splitting ───────────────────────────────────────────────────────────

def split_into_batches(
    items: list[Any],
    *,
    budget_tokens: int,
    envelope_tokens: int = 0,
    min_batch_size: int = 1,
) -> list[list[Any]]:
    """Split items into token-safe batches.

    Greedily fills each batch up to (budget_tokens - envelope_tokens).
    Each item's token count is estimated from its JSON serialisation.
    Guarantees at least min_batch_size items per batch when possible.
    """
    available = max(budget_tokens - envelope_tokens, 1)
    batches: list[list[Any]] = []
    current: list[Any] = []
    current_tokens = 0

    for item in items:
        item_tokens = estimate_tokens(json.dumps(item, separators=(",", ":")))
        if current and current_tokens + item_tokens > available:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += item_tokens

    if current:
        batches.append(current)

    # Ensure minimum batch size is respected where possible
    if min_batch_size > 1:
        merged: list[list[Any]] = []
        for batch in batches:
            if merged and len(merged[-1]) < min_batch_size:
                merged[-1].extend(batch)
            else:
                merged.append(batch)
        batches = merged

    return batches or [[]]


# ── Item fitting ──────────────────────────────────────────────────────────────

def fit_items_by_tokens(
    items: list[dict],
    *,
    budget_tokens: int,
    envelope_tokens: int = 0,
    min_items: int = 1,
) -> list[dict]:
    """Return the largest prefix of items that fits the token budget.

    Replaces the old character-based fit_json_items().
    Character limits are no longer the primary constraint here.

    Args:
        items: List of dicts to fit.
        budget_tokens: Token budget available for item content.
        envelope_tokens: Tokens consumed by the prompt framing (excluded from budget).
        min_items: Always include at least this many items even if over budget.
    """
    available = max(budget_tokens - envelope_tokens, 1)
    selected: list[dict] = []
    current_tokens = 0

    for item in items:
        item_tokens = estimate_tokens(json.dumps(item, separators=(",", ":")))
        if selected and current_tokens + item_tokens > available:
            break
        selected.append(item)
        current_tokens += item_tokens

    if len(selected) < min_items:
        return items[: min(min_items, len(items))]
    return selected


# ── Priority-based context reduction ─────────────────────────────────────────

# Priority levels: lower number = removed LAST (most important)
# Priority 1 (NEVER remove): evidence_id, chunk_index, provenance
# Priority 2 (remove last):  findings, claims, entities
# Priority 3:                 dates, amounts, identifiers
# Priority 4:                 relationships, link reasons
# Priority 5:                 context snippets, excerpts
# Priority 6 (remove first): verbose text, optional metadata

_DEFAULT_FIELD_PRIORITIES: dict[str, int] = {
    # Never-remove provenance fields (virtual — handled separately)
    "evidence_id": 1,
    "chunk_index": 1,
    "evidence_refs": 1,
    "provenance_id": 1,
    # Findings / entities (remove last)
    "key_findings": 2,
    "findings": 2,
    "people": 2,
    "organisations": 2,
    "identifiers": 3,
    "amounts": 3,
    "dates": 3,
    "times": 3,
    "locations": 3,
    "contact_info": 3,
    # Relationships / reasons
    "reason": 4,
    "relationships": 4,
    # Context snippets
    "key_statements": 5,
    "statements": 5,
    "snippet": 5,
    "excerpts": 5,
    # Verbose / optional (remove first)
    "note_hint": 6,
    "description": 6,
    "text": 6,
    "verbose_context": 6,
}

_PROTECTED_KEYS = frozenset({"evidence_id", "chunk_index", "evidence_refs", "provenance_id"})


def truncate_by_semantic_priority(
    payload: dict,
    *,
    budget_tokens: int,
    field_priorities: dict[str, int] | None = None,
) -> dict:
    """Reduce a dict's content to fit the token budget.

    Removes fields in descending priority order (highest number first).
    Never removes provenance / evidence_id / chunk_index fields.

    Args:
        payload: The dict to reduce.
        budget_tokens: Target token count.
        field_priorities: Optional override of the default priority map.
    """
    priorities = field_priorities or _DEFAULT_FIELD_PRIORITIES

    if estimate_tokens(json.dumps(payload, separators=(",", ":"))) <= budget_tokens:
        return payload

    result = dict(payload)
    # Sort removable fields from highest priority number (remove first) to lowest
    removable = sorted(
        [k for k in result if k not in _PROTECTED_KEYS],
        key=lambda k: priorities.get(k, 5),
        reverse=True,
    )
    for field in removable:
        if estimate_tokens(json.dumps(result, separators=(",", ":"))) <= budget_tokens:
            break
        value = result.get(field)
        if isinstance(value, list) and len(value) > 1:
            # Trim list progressively before removing entirely
            while len(result[field]) > 1:
                result[field] = result[field][: len(result[field]) // 2]
                if estimate_tokens(json.dumps(result, separators=(",", ":"))) <= budget_tokens:
                    break
        if estimate_tokens(json.dumps(result, separators=(",", ":"))) > budget_tokens:
            result.pop(field, None)

    return result


# ── Semantic chunk fitting ────────────────────────────────────────────────────

def fit_chunk_text(text: str, max_tokens: int) -> str:
    """Fit text to a token budget at semantic boundaries.

    Tries paragraph → sentence → word boundaries before any character-level cut.
    Preserves complete chunk whenever possible.

    Never truncates mid-word or mid-sentence if a coarser boundary is available.
    """
    if estimate_tokens(text) <= max_tokens:
        return text  # Preserve complete chunk — most common case

    def _fit_units(units: list[str], joiner: str) -> str | None:
        fitted: list[str] = []
        for unit in units:
            candidate = joiner.join([*fitted, unit])
            if estimate_tokens(candidate) > max_tokens:
                break
            fitted.append(unit)
        return joiner.join(fitted) if fitted else None

    # 1. Paragraph boundaries
    result = _fit_units(text.split("\n\n"), "\n\n")
    if result:
        return result

    # 2. Sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", text)
    result = _fit_units(sentences, " ")
    if result:
        return result

    # 3. Word boundaries (last resort before character cut)
    result = _fit_units(text.split(), " ")
    if result:
        return result

    # 4. Absolute last resort — character boundary (avoids mid-word only if possible)
    char_limit = max_tokens * 3  # very conservative estimate
    return text[:char_limit]


# ── Legacy compatibility ──────────────────────────────────────────────────────

def compact_json_size(value: Any) -> int:
    """Approximate request size using compact JSON. (Legacy — prefer estimate_tokens.)"""
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def fit_json_items(
    items: list[dict],
    *,
    budget_chars: int,
    envelope_chars: int = 0,
    min_items: int = 1,
) -> list[dict]:
    """Character-based item fitting. (Legacy — callers should migrate to fit_items_by_tokens.)"""
    if budget_chars <= 0:
        return items[:min_items]
    selected: list[dict] = []
    for item in items:
        candidate = [*selected, item]
        if selected and compact_json_size(candidate) + envelope_chars > budget_chars:
            break
        selected.append(item)
    if len(selected) < min_items:
        return items[: min(min_items, len(items))]
    return selected
