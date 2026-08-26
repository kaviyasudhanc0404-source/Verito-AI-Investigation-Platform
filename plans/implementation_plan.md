# Final Revised Implementation Plan: Token-Aware Pipeline with Evidence-Grounding Gate

> **Status**: Ready for review before implementation.  
> **Do NOT begin coding until this plan is explicitly approved.**

---

## 1. Architecture Changes

### Current Architecture

```
Collector → Analyzer → Timeline → Linker → Subject → Report → Save/Display
```

Each agent independently manages its own character budgets, has no shared token estimation, and the report is saved immediately after generation with only basic grounding checks.

### Revised Architecture

```
                      ┌───────────────────────────┐
                      │  Shared Token-Budget Mgr   │
                      │  (used by ALL 5 agents)    │
                      └────────────┬──────────────┘
                                   │
Collector ─→ Analyzer ─→ Timeline ─→ Linker ─→ Subject ─→ Report Generator
                │                                               │
                │  (chunk classification:                       │
                │   LLM or deterministic,                       │
                │   based on actual content)              ┌─────▼──────────────┐
                │                                         │  GROUNDING GATE    │
                │                                         │  (mandatory, fully │
                │                                         │   auditable)       │
                │                                         └─────┬──────────────┘
                │                                               │
                │         ┌─────────────────────────────────────▼──┐
                │         │  Structured Telemetry (every failure)  │
                └─────────▶  logged: agent, evidence_ids, batch,   │
                           │  attempt, error code, token estimate,  │
                           │  fallback action                       │
                           └────────────────────────────────────────┘
                                                │
                                          Save / Display
```

**Key principles**:

1. **Shared token-budget utility** consumed by all 5 downstream agents — configurable, not hard-coded to a specific TPM value
2. **Hybrid Evidence Analyzer** — routes chunks to LLM or deterministic based on actual content signal; routing counts are observed outcomes of the evidence, not targets to hit
3. **Hard same-context invariant** — Timeline Builder never combines date + time from different contexts
4. **Date-type classification** — EVENT / DOCUMENT / REFERENCE / METADATA / AMBIGUOUS
5. **Structured telemetry** — every LLM failure is logged with full diagnostic detail; no failure is hidden or silently converted
6. **Final Grounding Gate** — mandatory, fully auditable; every removal/downgrade has a structured reason; never removes valid deterministic observations
7. **Batch synthesis + merge** for Timeline and any agent whose payload exceeds the per-request budget

---

## 2. File-by-File Changes

| File | Action | Summary |
|:-----|:-------|:--------|
| [`context_budget.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/utils/context_budget.py) | **REWRITE** | Token estimation, configurable budget model, fit-by-tokens, split-into-batches, priority-based reduction, payload validation |
| [`config.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/core/config.py) | MODIFY | Add configurable token budget settings (per-request max, output reserve, system prompt reserve, safety margin) |
| [`llm_service.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/services/llm_service.py) | MODIFY | `ContextTooLargeError` exception; structured 413 re-raise; structured telemetry on every failure |
| [`evidence_analyzer.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/agents/evidence_analyzer.py) | MODIFY | Hybrid pipeline (content-based routing), token-based batching, compact schema, semantic chunk fitting, adaptive output budget, per-failure telemetry |
| [`timeline_builder.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/agents/timeline_builder.py) | MODIFY | Same-context invariant, date-type classification, batch synthesis + merge, provenance-preserving dedup, token-aware budgets, per-failure telemetry |
| [`evidence_linker.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/agents/evidence_linker.py) | MODIFY | Generic-term stoplist, specific-identifier gating, token-aware budgets, per-failure telemetry |
| [`subject_analyzer.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/agents/subject_analyzer.py) | MODIFY | Integrate shared token-budget system, priority-based context reduction, per-failure telemetry |
| [`report_generator.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/agents/report_generator.py) | MODIFY | Compact case state, section-level synthesis, no blind truncation, token-aware budgets, per-failure telemetry |
| **[NEW]** [`grounding_gate.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/utils/grounding_gate.py) | **CREATE** | Auditable final validation layer with structured `GateViolation` records for every removal/downgrade |
| [`investigation_graph.py`](file:///d:/VERITO/Verito-AI-Investigation-Platform/backend/app/workflows/investigation_graph.py) | MODIFY | Wire grounding gate between report generation and save/emit |

---

## 3. New / Modified Constants

### `config.py` additions

```python
class Settings:
    # Token budget configuration — all adjustable via .env
    # These control per-request sizing, NOT TPM/rate limits.
    llm_per_request_max_tokens: int = 16384    # Conservative per-request ceiling
    llm_system_prompt_reserve: int = 600       # Reserved for system prompt
    llm_default_output_reserve: int = 2000     # Default output token reservation
    llm_safety_margin: int = 300               # Estimation buffer
    # Derived: effective_input_budget = 16384 - 600 - 2000 - 300 = 13484
```

### `evidence_analyzer.py` — constants replaced

```python
# OLD (fixed char limits):
_MAX_BATCH_CHARS = 9000
_MAX_CHUNK_CHARS = 3500

# NEW (token-aware, content-driven):
_MAX_CHUNK_TOKENS = 1500      # Token budget per chunk text before semantic fitting
_PER_CHUNK_OUTPUT_ESTIMATE = 200  # ~tokens expected in LLM output per chunk
_LLM_OUTPUT_ENVELOPE = 150    # JSON envelope overhead in output token estimate
# No fixed batch token limit — batch size is computed dynamically from budget
```

### `timeline_builder.py` — constants replaced

```python
# OLD:
_MAX_CANDIDATES_FOR_LLM = 80
_LLM_CONTEXT_BUDGET_CHARS = 14000
_MAX_TEXT_SNIPPET_CHARS = 280

# NEW:
_MAX_TEXT_SNIPPET_CHARS = 120   # Compact candidate context
# No fixed candidate count — all candidates are included via batch synthesis + merge
# Budget is computed from shared context_budget utility
```

### `evidence_linker.py` — constants replaced

```python
# OLD:
_MAX_CANDIDATES_FOR_LLM = 60
_LLM_CONTEXT_BUDGET_CHARS = 14000

# NEW:
# Budget computed from shared utility; no hard candidate cap
# All candidates ranked by score; lowest-score dropped first if budget exceeded
```

### `subject_analyzer.py` — constants replaced

```python
# OLD:
_LLM_CONTEXT_BUDGET_CHARS = 12000

# NEW:
# Budget from shared utility; priority-based reduction replaces character cap
```

### `report_generator.py` — constants replaced

```python
# OLD:
_MAX_CONTEXT_CHARS = 18000

# NEW:
# Compact case state built from validated, ranked data; budget from shared utility
# Section-level synthesis if compact state still exceeds budget
```

---

## 4. Token-Budget Strategy

### 4a. Separating Four Distinct Concepts

| Concept | Definition | Example Value |
|:--------|:-----------|:-------------|
| **Model context window** | Maximum tokens the model can process (input + output combined) | 131,072 for `openai/gpt-oss-20b` |
| **Maximum completion tokens** | Maximum output the model can generate | 65,536 for `openai/gpt-oss-20b` |
| **Per-request input budget** | How many input tokens we use in a single API call — **configurable, derived from observed endpoint behaviour, not from TPM** | Default: 13,484 effective input |
| **Provider rate limits (TPM/RPM)** | Throughput limits across all requests per minute — a separate system-level concern | 8,000 TPM on Groq free tier (not a per-request limit) |

> [!IMPORTANT]
> TPM (Tokens Per Minute) limits how much throughput is allowed across all requests in a minute. It does **not** define the maximum size of any single request. The 413 "Request too large" error comes from the provider's per-request size enforcement, which is a separate constraint. The implementation must never hard-code TPM as a per-request token ceiling.

### 4b. Token Budget Calculation

```
per_request_max_tokens   (configurable, default 16384)
  − system_prompt_reserve  (configurable, default 600)
  − output_token_reserve   (per-agent or per-call, default 2000)
  − safety_margin          (configurable, default 300)
  ─────────────────────────────────────────────────────
= effective_input_budget  (available for user prompt / serialized context)
```

**Mandatory constraint**: `estimated_input_tokens + system_prompt_tokens + requested_output_tokens` must always be verified to fit within the model's context limit before a request is sent. This is enforced by `validate_payload()` before every LLM call.

> [!IMPORTANT]
> **Do NOT solve JSON validation (400) failures by simply increasing `max_tokens`.** The correct responses are, in priority order:
> 1. **Reduce batch size** — fewer chunks → shorter expected output → smaller `max_tokens` needed
> 2. **Use the compact schema** — short-key mapping reduces output token volume
> 3. **Compute `max_tokens` adaptively per batch** via `_output_budget_for_batch()` — never a global constant
>
> Increasing `max_tokens` without also reducing input context shrinks the effective input budget and risks exceeding the model's context limit on the combined (input + output) side. Smaller batches and compact schemas are the primary remedies.

Agents can request a custom output reservation:

```python
# Each call computes its own budget:
budget = get_token_budget(output_reserve=1200)
# budget.effective_input = 16384 - 600 - 1200 - 300 = 14284

budget = get_token_budget(output_reserve=2500)
# budget.effective_input = 16384 - 600 - 2500 - 300 = 12984
```

Changing `llm_per_request_max_tokens` in `.env` propagates to all agents automatically — no per-agent code changes needed.

### 4c. Token Estimation

A multi-factor approach rather than a single character-to-token ratio:

```python
def estimate_tokens(text: str) -> int:
    """Estimate token count using word-based estimation with JSON/number overhead."""
    if not text:
        return 0
    words = text.split()
    word_count = len(words)
    base_estimate = int(word_count * 1.3)     # ~1.3 tokens per word
    json_chars = sum(1 for c in text if c in '{}[]":,')
    json_overhead = json_chars // 3            # ~1 extra token per 3 JSON punctuation chars
    digit_sequences = len(re.findall(r'\d+', text))
    digit_overhead = digit_sequences           # Numeric strings tokenize densely
    estimate = base_estimate + json_overhead + digit_overhead
    char_floor = len(text) // 5               # Absolute minimum
    char_ceiling = len(text) // 2             # Absolute maximum
    return max(char_floor, min(estimate, char_ceiling))
```

Character limits remain as **secondary safety nets** only.

### 4d. Priority-Based Context Reduction

When context exceeds the budget, the shared utility reduces content following a strict priority order. Items at the **top are removed last**:

```
Priority 1 (NEVER remove): evidence_id, chunk_index, provenance references
Priority 2 (remove last):  actual findings, claims, identified entities
Priority 3:                 dates, timestamps, amounts, specific identifiers
Priority 4:                 relationships, link reasons
Priority 5:                 context snippets, supporting excerpts
Priority 6 (remove first): verbose descriptions, full text, optional metadata
```

### 4e. Payload Validation

Before any LLM call:

```python
def validate_payload(messages, *, max_output_tokens, budget) -> PayloadValidation:
    """Returns .fits, .estimated_input_tokens, .estimated_total, .headroom, .reduction_needed"""
```

If validation fails, the **calling agent** (not the utility) decides how to reduce: split batch, trim by priority, or use section-level synthesis.

---

## 5. Evidence Analyzer Strategy

### 5a. Hybrid Pipeline Architecture

```
For each chunk:
      │
      ▼
┌──────────────────────────────────┐
│  _classify_chunk(text, filename) │
│  • Count narrative indicators    │
│  • Measure structural ratio      │
│  • Estimate extractable density  │
│  • Produce: classification +     │
│    reason + semantic_density     │
└──────────┬───────────────────────┘
           │
           ├── "llm_required" ──→ add to LLM batch queue
           │
           └── "deterministic_sufficient" ──→ run regex extraction
                                              (observations only — key_findings always [])
```

The count of chunks routed each way is an **observed outcome of the content**, not a target. If a future dataset has 70 LLM-required chunks and 3 deterministic chunks, that is correct behaviour. The system must not artificially force chunks into deterministic mode to reduce API calls.

### 5b. Chunk Classification Criteria

```python
def _classify_chunk(text: str, filename: str = "") -> tuple[str, str, float]:
    """Classify whether a chunk needs LLM analysis or deterministic extraction.

    Returns: (classification, reason, semantic_density)
    classification: "llm_required" | "deterministic_sufficient"

    Bias: default is LLM. Deterministic only when chunk is clearly
    structured/tabular with obvious extractable entities and no narrative content.
    """
    word_count = len(text.split())

    # Very short chunks — nothing semantic to extract
    if word_count < 10:
        return ("deterministic_sufficient", "too_short", 0.0)

    extractable_count = (
        len(_ID_RE.findall(text)) + len(_EMAIL_RE.findall(text))
        + len(_PHONE_RE.findall(text)) + len(_AMOUNT_RE.findall(text))
        + len(_ISO_DATE_RE.findall(text)) + len(_MONTH_DATE_RE.findall(text))
    )

    narrative_indicators = len(re.findall(
        r'\b(because|therefore|however|although|suggests?|indicates?|confirms?|'
        r'contradicts?|suspicious|unusual|pattern|relationship|according|'
        r'evidence|witness|statement|alleged|reported|observed|violation|'
        r'discrepancy|unauthorized|irregular|fraudulent|complaint)\b',
        text, re.IGNORECASE
    ))

    structural_lines = sum(
        1 for line in text.split('\n')
        if '|' in line or line.count(',') >= 3 or line.count('\t') >= 2
    )
    structural_ratio = structural_lines / max(len(text.split('\n')), 1)

    # Higher semantic_density → more likely to need LLM
    semantic_density = (narrative_indicators * 3 + word_count - extractable_count * 5) / max(word_count, 1)
    semantic_density = max(0.0, min(1.0, semantic_density))

    # Rule 1: ANY narrative content → always LLM
    if narrative_indicators >= 2:
        return ("llm_required", "narrative_content", semantic_density)

    # Rule 2: Clearly tabular/structured with extractable entities → deterministic sufficient
    if structural_ratio >= 0.6 and extractable_count >= 3:
        return ("deterministic_sufficient", "structured_tabular_data", semantic_density)

    # Rule 3: Entity-dense, low semantic content
    if semantic_density < 0.15 and extractable_count >= 5:
        return ("deterministic_sufficient", "entity_dense_low_semantic", semantic_density)

    # Default: LLM (prefer quality; never artificially force deterministic)
    return ("llm_required", "default_llm_preferred", semantic_density)
```

### 5c. Deterministic Extraction Rules

`key_findings` is always `[]` — **an enforced invariant**:

```python
def _deterministic_extract(text: str) -> dict:
    output = {key: [] for key in _ENTITY_KEYS}
    # ... existing regex extraction (emails, URLs, phones, dates, times,
    #     org suffixes, IDs, amounts, statement snippets) ...

    # INVARIANT: deterministic extraction never produces findings.
    # Findings require semantic interpretation. Only LLM analysis may produce them.
    output["key_findings"] = []   # Always empty — explicit enforcement
    return output
```

`key_statements` produced by deterministic extraction are **contextual snippets** (observations around extracted entities), not findings. They remain available as observations throughout the pipeline.

### 5d. Token-Based Batching for LLM Chunks

```python
def _batch_chunks_by_tokens(llm_chunks: list[dict], budget: TokenBudget) -> list[list[dict]]:
    """Group LLM-required chunks into token-safe batches.

    Rules:
    - Context-aware: same evidence_id stays together where possible.
    - Splits within evidence when a single evidence's chunks exceed budget.
    - Never artificially limits batch size to hit a minimum API-call count.
    - Batch size is determined solely by the token budget.
    """
    system_prompt_tokens = estimate_tokens(_SYSTEM_PROMPT)
    envelope_tokens = 80
    available = budget.effective_input - system_prompt_tokens - envelope_tokens

    batches, current_batch, current_tokens, current_eid = [], [], 0, None

    for chunk in llm_chunks:
        chunk_tokens = estimate_tokens(chunk["text"]) + 60  # +60 for metadata
        eid = chunk["evidence_id"]
        crosses_evidence = current_batch and current_eid != eid
        exceeds_budget = current_batch and current_tokens + chunk_tokens > available

        if crosses_evidence or exceeds_budget:
            if current_batch:
                batches.append(current_batch)
            current_batch, current_tokens = [], 0

        current_batch.append(chunk)
        current_tokens += chunk_tokens
        current_eid = eid

    if current_batch:
        batches.append(current_batch)
    return batches
```

### 5e. Adaptive Output Budget Per Batch

```python
def _output_budget_for_batch(batch: list[dict], budget: TokenBudget) -> int:
    """Compute output tokens needed for this specific batch."""
    needed = _LLM_OUTPUT_ENVELOPE + len(batch) * _PER_CHUNK_OUTPUT_ESTIMATE
    return min(needed, budget.output_reserve)
```

### 5f. Compact LLM Schema (Short Keys)

```python
_SHORT_KEY_MAP = {
    "ci": "chunk_index", "p": "people", "o": "organisations",
    "l": "locations", "d": "dates", "t": "times",
    "c": "contact_info", "id": "identifiers", "a": "amounts",
    "ks": "key_statements", "kf": "key_findings",
}
```

Applied only at LLM I/O boundary. Internal data structures always use full field names.

### 5g. Semantic Chunk Fitting

When a chunk's text exceeds the per-chunk token budget, trim at semantic boundaries:

```python
def _fit_chunk_text(text: str, max_tokens: int) -> str:
    """Fit text to token budget at paragraph → sentence → word boundaries."""
    if estimate_tokens(text) <= max_tokens:
        return text  # Preserve complete chunk whenever possible
    for split_fn, joiner in [
        (lambda t: t.split('\n\n'), '\n\n'),
        (lambda t: re.split(r'(?<=[.!?])\s+', t), ' '),
        (lambda t: t.split(), ' '),
    ]:
        fitted = _fit_units(split_fn(text), max_tokens, joiner=joiner)
        if fitted:
            return fitted
    return text[:max_tokens * 3]  # Absolute last resort
```

---

## 6. Timeline Validation Strategy

### 6a. Hard Same-Context Invariant

**Mandatory with no exceptions.**

A combined timestamp (date + time in `sort_time`) may only be constructed when date and time originate from the **same evidence context**:

| Context boundary | Permitted |
|:-----------------|:----------|
| `same_sentence` | ✅ Yes |
| `same_record` | ✅ Yes (CSV row, JSON object, email header, log line) |
| `iso_datetime` | ✅ Yes (ISO 8601 combined string inherently co-located) |
| `same_paragraph` | ✅ Only if paragraph < 200 chars (single coherent statement) |
| Different paragraphs | ❌ Never |
| Document header date + body time | ❌ Never |
| Date from one email + time from another | ❌ Never |

When the check fails:
- Candidate is date-only (`precision = "day"`)
- `same_context_verified = False`
- `sort_time` contains date portion only (no time)
- Time is stored in provenance but never combined into `sort_time`

### 6b. Date-Type Classification

```python
# EVENT   → normal timeline event
# DOCUMENT → "Memo dated 20 Feb 2026" — included as UNCONFIRMED context
# REFERENCE → "Policy effective from..." — included as UNCONFIRMED
# METADATA  → file creation/modification — excluded from main timeline, stored in provenance
# AMBIGUOUS → included as UNCONFIRMED, confidence = low
```

Classification is based on:
1. Keyword patterns in the excerpt (e.g., "issued on", "created at", "effective from")
2. File extension heuristics (`.eml`/`.csv`/`.json` → likely EVENT; `.pdf`/`.docx` header → likely DOCUMENT)
3. Position within content (date in first 500 chars of a PDF → likely DOCUMENT)

### 6c. Provenance-Aware Deduplication

Groups by `(evidence_id, sort_time, date_type)` — keeps the candidate with the richest context (most statements, longest snippet, `same_context_verified = True`). Same event from **different evidence** keeps both candidates (different provenance = distinct timeline entries).

### 6d. Batch Synthesis + Merge

When candidates exceed a single request's token budget:
1. Split compact candidates into token-safe batches using shared `split_into_batches()`
2. Send each batch to LLM independently
3. Collect all synthesised events
4. Merge: deduplicate by `(sort_time, frozenset(refs))`, keep the richer event where both survive

If a batch itself hits `ContextTooLargeError`, halve it and retry.

### 6e. Post-Synthesis Validation

Every synthesised event must:
- Have valid evidence refs
- Have a `sort_time` traceable to a candidate (no LLM-invented timestamps)
- Pass the cross-context check against candidate provenance
- Not duplicate an already-validated event

---

## 7. Evidence Linker Validation Strategy

### 7a. Generic Term Stoplist

```python
_GENERIC_IDENTIFIER_TERMS = frozenset({
    "investigation", "evidence", "vendor", "employee", "policy",
    "invoice", "invoices", "account", "email", "report", "audit",
    "document", "file", "record", "system", "user", "data",
    "status", "completed", "pending", "approved", "total", "active",
    "table", "row", "column", "field", "value", "type", "date",
    "number", "amount", "name", "description", "note", "comment",
})
```

### 7b. Specific Identifier Gating

An identifier qualifies only if it:
1. Is not in the generic stoplist
2. Has length ≥ 4 after normalisation
3. Contains at least one digit (e.g., `PO-2026-0342`, `INV-KBS-0110`, `MIL-EMP-0587`)

Applied at **two points**:
1. In `_build_entity_index()` — generic terms never enter the index
2. In `_entity_support()` scoring — near-zero score for any that slip through without digits

### 7c. Strength Classification

`strong` requires specific shared identifiers (with digits) or contact info plus multiple support types. Generic words cannot produce strong or moderate links regardless of score.

### 7d. Token-Aware LLM Context

Replace `_LLM_CONTEXT_BUDGET_CHARS = 14000` with shared token budget. Candidates are ranked by score; lowest-score candidates are dropped first if budget is exceeded (never provenance or IDs).

---

## 8. Subject Analyzer Integration

### 8a. Adopt Shared Token Budget

Replace `_LLM_CONTEXT_BUDGET_CHARS = 12000` with:

```python
budget = get_token_budget(output_reserve=1500)
compact = fit_items_by_tokens(
    candidates,
    budget_tokens=budget.effective_input,
    envelope_tokens=estimate_tokens(_SYSTEM_PROMPT) + 60,
    min_items=min(8, len(candidates)),
)
validation = validate_payload(messages, max_output_tokens=1500, budget=budget)
if not validation.fits:
    compact = truncate_by_semantic_priority(compact, ...)
```

### 8b. Priority-Based Reduction

When reducing subject candidates:
1. `candidate_id`, `name`, `evidence_refs` — never removed
2. `involvement_indicator`, `confidence`, `mention_count`
3. `contact_info`, `organisations`, `locations`
4. `timeline_refs`, `link_refs`
5. `provenance` excerpts
6. `note_hint` — removed first

Subject Analyzer currently fits within typical budgets for small investigations. Adopting the shared system makes it consistent and future-proof for larger investigations.

---

## 9. Report Generator Changes

### 9a. Compact Validated Case State

Build from validated, ranked data — not raw agent outputs:

```python
def _build_compact_case_state(*, investigation_prompt, collector_output, analyzer_output,
                                timeline_output, linker_output, subject_output, valid_evidence_ids):
    return {
        "query": investigation_prompt[:200],
        "evidence_ids": sorted(valid_evidence_ids),
        "findings": _top_findings(analyzer_output, valid_evidence_ids, limit=12),
        "timeline": {
            "window": timeline_output.get("time_window", "Unknown"),
            "events": _top_events(timeline_output, limit=12),
            "fact_count": timeline_output.get("fact_count", 0),
        },
        "links": _top_links(linker_output, valid_evidence_ids, limit=8),
        "subjects": _compact_subjects(subject_output, valid_evidence_ids, limit=8),
        "gaps": _evidence_gaps(collector_output, analyzer_output, timeline_output),
    }
```

Each `_top_*` function ranks by **score/confidence/evidence count**, not insertion order. No first-N bias.

### 9b. Token-Aware Synthesis Decision

```python
case_state_tokens = estimate_tokens(json.dumps(case_state))
budget = get_token_budget(output_reserve=2500)

if case_state_tokens <= budget.effective_input:
    raw_report = await _single_pass_synthesis(case_state, budget)
else:
    raw_report = await _section_level_synthesis(case_state, budget)
```

### 9c. Section-Level Synthesis

Splits synthesis into 3 independent calls (findings+summary, timeline, connections+subjects) when the full case state exceeds the budget. Each call independently fits within the per-request token budget.

### 9d. No Blind Truncation

`synthesis_context[:_MAX_CONTEXT_CHARS]` is replaced with `truncate_by_semantic_priority()`. Provenance and findings are preserved as long as possible; verbose descriptive metadata is removed first.

---

## 10. Final Grounding Gate

### 10a. Architecture Position

```
Report Generator
      │
      ▼ (raw InternalReport)
┌────────────────────────────────┐
│        GROUNDING GATE          │
│                                │
│  For every claim:              │
│  • Validate evidence refs      │
│  • Check provenance            │
│  • Enforce invariants          │
│  • Record each action in       │
│    structured audit trail      │
│                                │
│  Never silently discard.       │
│  Never remove valid            │
│  deterministic observations.   │
└───────────────┬────────────────┘
                │ (validated InternalReport + GateResult with full audit)
          Save / Display
```

### 10b. Violation Schema

Every violation is a structured record with enough information to explain what happened and why:

```python
@dataclass
class GateViolation:
    section: str           # "findings" | "timeline" | "connections" | "subjects" | "unresolved"
    claim: str             # First 120 chars of the claim text
    evidence_refs: list[str]  # Evidence IDs referenced by the claim
    reason: str            # See reason catalogue below
    action: str            # "removed" | "downgraded" | "rewritten" | "retained"
    detail: str            # Optional: specific entity/value that caused the violation
```

**Permitted reasons**:

| Reason | Meaning |
|:-------|:--------|
| `unsupported_claim` | Claim text has no traceable evidence support |
| `missing_provenance` | No evidence_ids attached to the item |
| `invalid_evidence_reference` | Referenced evidence_id does not exist |
| `cross_context_date_time_combination` | Timeline event has date+time from different source contexts |
| `duplicate_event` | Identical event identity already in validated set |
| `implausible_timestamp` | sort_time not traceable to any candidate |
| `weak_link_support` | Connection downgraded because supporting identifiers are non-specific |
| `generic_identifier_link` | Connection based solely on generic terms |
| `no_linker_support` | Connection has no corresponding linker candidate |
| `deterministic_finding_not_allowed` | Finding was produced without LLM analysis (invariant violation) |
| `insufficient_evidence` | Evidence refs exist but do not support the specific claim |
| `accusatory_claim` | Finding contains impermissible guilt/culpability language |
| `invented_entity` | Claim references entities not found in any evidence |
| `guilt_implication` | Subject note contains impermissible implication language |
| `unsupported_allegation` | Unresolved question converts missing evidence into a factual claim |
| `duplicate_question` | Unresolved question is identical to another already included |

**Permitted actions**:

| Action | Meaning |
|:-------|:--------|
| `removed` | Item entirely excluded from the final report |
| `downgraded` | Strength/confidence reduced (e.g., strong → weak, FACT → UNCONFIRMED) |
| `rewritten` | Accusatory/implication language neutralised; item retained |
| `retained` | Item passed validation with no changes |

### 10c. Validation Rules

#### A. Key Findings

1. Must have at least one valid `evidence_id` → else `removed` / `missing_provenance`
2. `evidence_id` must exist in the investigation → else `removed` / `invalid_evidence_reference`
3. Key terms in the finding text must be traceable to referenced evidence in analyzer output → else `downgraded` / `insufficient_evidence`
4. No accusatory language (`guilty`, `culpable`, `perpetrator`, etc.) → else `removed` / `accusatory_claim`
5. No entities invented by the LLM that don't appear in any evidence → else `removed` / `invented_entity`

> [!IMPORTANT]
> Deterministic observations (`key_statements`) that have valid provenance are **never removed** by the gate. Only unsupported claims generated without evidence support are rejected. The gate must distinguish between "a finding has no evidence support" and "a deterministic observation has valid provenance." The latter is always retained.

#### B. Timeline

1. Must have valid evidence refs → else `removed` / `missing_provenance`
2. No cross-context date/time combinations (checked against candidate `same_context_verified`) → else `removed` / `cross_context_date_time_combination`
3. No duplicates by event identity (not just string equality) → else `removed` / `duplicate_event`
4. `sort_time` must trace to a candidate's sort_time → else `downgraded` / `implausible_timestamp`

#### C. Evidence Connections

1. Both `a` and `b` must exist in `valid_evidence_ids` → else `removed` / `invalid_evidence_reference`
2. Reason must not rely solely on generic terms → else `removed` / `generic_identifier_link`
3. Connection must have a corresponding linker candidate with real support → else `removed` / `no_linker_support`

#### D. Subjects

1. Must have valid evidence refs → else `removed` / `missing_provenance`
2. Person's name must appear in analyzer output for referenced evidence → else `removed` / `insufficient_evidence`
3. Note must not imply guilt/culpability → else `rewritten` / `guilt_implication`

#### E. Unresolved Questions

1. No duplicates → else `removed` / `duplicate_question`
2. Must not convert missing evidence into a factual allegation → else `removed` / `unsupported_allegation`

### 10d. Gate Result Schema

```python
@dataclass
class GateResult:
    passed: bool                     # True if ≥1 validated finding
    report: InternalReport           # Validated (possibly modified) report
    violations: list[GateViolation]  # Complete audit trail
    stats: dict                      # Counts per section
```

Stored report includes:
```json
{
  "grounding_gate": {
    "findings_validated": 11,
    "findings_removed": 1,
    "findings_downgraded": 0,
    "timeline_validated": 18,
    "timeline_removed": 2,
    "connections_validated": 8,
    "connections_removed": 0,
    "subjects_validated": 2,
    "subjects_rewritten": 0
  },
  "grounding_violations": [
    {
      "section": "timeline",
      "claim": "Arvind Kumaraswamy logged into system at 09:12:14...",
      "evidence_refs": ["E-005"],
      "reason": "cross_context_date_time_combination",
      "action": "removed",
      "detail": "date from chunk 0 (paragraph 1), time from chunk 2 (transaction row)"
    }
  ]
}
```

### 10e. Gate Does Not Remove Valid Deterministic Observations

If an upstream LLM synthesis failed and the system fell back to deterministic extraction, the deterministic observations (entities, dates, identifiers, statement snippets) are **retained in the report** as observations — not silently dropped. They are not promoted to findings (which require LLM semantic interpretation), but they remain accessible and provenance-tracked.

```python
# The gate validates claims, not observations.
# Observations from deterministic extraction have their own field (key_statements)
# and are not evaluated under the "finding" validation rules.
```

### 10f. Integration with Pipeline

In `investigation_graph.py`, after report generation:

```python
gate_result = await run_grounding_gate(
    report=internal_report,
    valid_evidence_ids=valid_evidence_ids,
    analyzer_output=s["analyzer_output"],
    timeline_output=s["timeline_output"],
    linker_output=s["linker_output"],
)

# gate_result.report is the validated version — always use this
internal_report = gate_result.report

generation_status = (
    "grounding_gate_failed" if not gate_result.passed
    else "grounding_gate_adjusted" if gate_result.violations
    else "complete"
)
```

---

## 11. Error Handling, Retry Strategy, and Telemetry

### 11a. LLM Service: Structured 413 Error

```python
class ContextTooLargeError(ServiceException):
    """Raised when a request payload exceeds the provider's per-request token limit."""
    def __init__(self, estimated_tokens: int, limit_tokens: int, original_message: str = ""):
        self.estimated_tokens = estimated_tokens
        self.limit_tokens = limit_tokens
        self.original_message = original_message   # Never hide the original error
        super().__init__(
            f"Request ~{estimated_tokens} estimated tokens exceeds limit ~{limit_tokens}. "
            f"Original: {original_message}"
        )
```

The LLM service **does not truncate or retry** — it raises `ContextTooLargeError`. Each agent handles it.

### 11b. Structured Telemetry for Every LLM Failure

**Every failed LLM request must emit a structured log entry.** No failure is hidden or silently converted. The log is useful for diagnosing future model/provider changes.

**Required fields for every failure log**:

```python
@dataclass
class LLMFailureTelemetry:
    agent_name: str              # "evidence_analyzer" | "timeline_builder" | etc.
    evidence_ids: list[str]      # Evidence IDs in this request, if applicable
    batch_number: int | None     # e.g., 4 of 15
    batch_total: int | None      # e.g., 15
    chunk_indices: list[str]     # e.g., ["E-001:4", "E-001:5", "E-001:6"]
    attempt_number: int          # 1, 2, 3 ...
    model_name: str              # From config, e.g., "openai/gpt-oss-20b"
    error_code: int | str        # HTTP status code: 400, 413, 429, etc.
    error_category: str          # "json_validation" | "context_too_large" | "rate_limit" | "timeout" | "unknown"
    estimated_input_tokens: int  # From estimate_tokens()
    requested_output_tokens: int # max_tokens passed to the call
    payload_size_chars: int      # len(json.dumps(messages))
    was_retried: bool            # Did the agent retry after this failure?
    retry_action: str | None     # "split_batch" | "reduce_context" | "reduce_batch_size" | None
    fallback_action: str | None  # "deterministic_extraction" | "section_synthesis" | "skip" | None
```

**Example log output**:

```
[TELEMETRY] LLM failure — Evidence Analyzer
  batch=4/15  attempt=1  model=openai/gpt-oss-20b
  chunks=E-001:4,E-001:5,E-001:6
  status=400  category=json_validation
  estimated_input_tokens=5420  requested_output_tokens=2000  payload_chars=18340
  retry=True  retry_action=split_batch  fallback=None

[TELEMETRY] LLM failure — Timeline Builder
  batch=2/3  attempt=1  model=openai/gpt-oss-20b
  evidence_ids=E-001,E-003,E-005,E-008,E-011
  status=413  category=context_too_large
  estimated_input_tokens=14200  requested_output_tokens=1500  payload_chars=51000
  retry=True  retry_action=reduce_context  fallback=None
```

**Security**: API keys, passwords, and secrets must **never** appear in telemetry logs. Only model name, token counts, and error metadata are logged.

**Telemetry emission**: Telemetry logs are emitted via the existing `add_line()` mechanism using `kind="telemetry"` or written to Python's `logging` system at `WARNING` level. They are **not** emitted as SSE events to the frontend.

### 11c. Agent-Level Retry Behaviour

| Agent | On ContextTooLargeError | On 400 JSON error | On 429 Rate Limit |
|:------|:------------------------|:------------------|:------------------|
| Evidence Analyzer | Split batch in half, retry each half; log `retry_action=split_batch` | Reduce batch size by 1 chunk, retry; log `retry_action=reduce_batch_size` | Exponential backoff (existing) |
| Timeline Builder | Split synthesis batch further, retry; log `retry_action=reduce_context` | Retry same batch; if fails again, skip synthesis for that batch and use deterministic event extraction | Exponential backoff |
| Evidence Linker | Drop lowest-score candidates until fits, retry; log `retry_action=reduce_context` | Retry once; if fails, use `_fallback_synthesis()` | Exponential backoff |
| Subject Analyzer | Priority-based field reduction, retry; log `retry_action=reduce_context` | Retry once; if fails, use `_fallback_synthesis()` | Exponential backoff |
| Report Generator | Switch to section-level synthesis; log `retry_action=section_synthesis` | Retry individual section; if fails, use deterministic fallback section | Exponential backoff |

### 11d. Fallback Hierarchy (All Agents)

```
0. validate_payload() before sending — if does not fit, reduce (split/trim) first
1. Send LLM request
2. If ContextTooLargeError (413):
     → classify error, emit structured telemetry (retry_action = "split_batch" | "reduce_context")
     → reduce (split / drop lowest-priority items)
     → retry once
     → if retry also 413: emit telemetry (fallback_action), use controlled fallback
3. If 400 JSON validation:
     → classify error, emit structured telemetry (retry_action = "reduce_batch_size")
     → reduce batch by removing 1 chunk, recompute output budget via _output_budget_for_batch()
     → do NOT simply increase max_tokens globally
     → retry once
     → if retry also 400: emit telemetry (fallback_action = "deterministic_extraction"), use deterministic
4. If 429 rate limit:
     → emit telemetry (retry_action = "exponential_backoff"), wait and retry
5. If all retries exhausted:
     → emit telemetry (fallback_action = final action taken)
     → use controlled fallback appropriate to the agent
6. If deterministic fallback produces nothing:
     → mark chunk as failed, record in agent lines and telemetry
```

**Every provider error is classified, logged with structured telemetry, retried or reduced when appropriate, and then either succeeds or uses a controlled fallback.** No error is ever unhandled or unlogged.

Deterministic fallback is a last resort. The fallback rate for LLM-routed chunks is expected to be low for typical investigation datasets; it is not artificially capped.

---

## 12. API-Call Expectations

### Expected Call Range for the 12-File / 73-Chunk Dataset

These are **observed estimates** based on the current dataset's content characteristics. They are not implementation targets. The system routes based on actual content; a different dataset may produce more or fewer calls, and that is correct behaviour.

| Agent | Estimated Range | Basis |
|:------|:---------------|:------|
| Evidence Analyzer | 10–20 calls | Depends on how many chunks classify as `llm_required` and how many fit per token-safe batch |
| Timeline Builder | 2–5 calls | Depends on candidate count and token density after compact representation |
| Evidence Linker | 1–2 calls | Depends on candidate count and support richness |
| Subject Analyzer | 1–2 calls | Depends on person count and evidence richness |
| Report Generator | 1–3 calls | Single-pass if compact state fits; otherwise 2–3 section-level calls |
| **Total estimate** | **~15–30 calls** | Benchmark for this dataset only — not a hard limit |

**Comparison with current**:
- Current: ~17 calls total, ~7 produce 400/413 errors → only ~10 calls actually succeed
- Revised: ~15–30 calls total, 0 unhandled errors → every call either succeeds or produces structured telemetry and a controlled fallback

> [!IMPORTANT]
> The `<50 calls` figure is a **benchmark for the current 12-file test dataset only**. It is not a hard requirement, a cut-off threshold, or an optimisation target. Evidence quality and correctness take absolute priority. If a future dataset requires 60 successful LLM calls to produce a correctly grounded report, that is the right outcome. The system must never artificially force deterministic routing, reduce batch quality, or skip LLM analysis simply to stay below a call count.

---

## 13. Verification / Test Plan

### 13a. Evidence Collector

| Test | Expected Result |
|:-----|:----------------|
| Files indexed | 12/12 |
| Chunks stored in Qdrant | 73 |
| Failed items | 0 |

### 13b. Evidence Analyzer

| Test | Expected Result |
|:-----|:----------------|
| No one-call-per-chunk regression | Batch sizes > 1 except for single-chunk evidence items (E-004, E-010) |
| LLM batch success rate | ≥95% of batches succeed on first or second attempt |
| Routing is based on actual content | Confirmed by inspecting `classification_reason` in logs — no artificial forcing |
| `key_findings` from deterministic extraction | Always `[]` — no fake "Identifier observed" findings |
| All chunks have `evidence_id` + `chunk_index` provenance | 73/73 |
| Telemetry log present for every failure | Yes — `kind="telemetry"` entries in agent lines |
| Telemetry includes estimated_input_tokens, error_code, retry_action | Yes — validated in at least one injected failure test |

### 13c. Timeline Builder

| Test | Expected Result |
|:-----|:----------------|
| Cross-context date/time combinations | 0 |
| Duplicate timeline events | 0 |
| False timestamps (invented by LLM without candidate support) | 0 |
| Every event has valid evidence provenance | 100% |
| `same_context_verified` field present on all candidates | Yes |
| Date-type classification present | Yes (EVENT / DOCUMENT / REFERENCE / METADATA / AMBIGUOUS) |
| Document-header dates not treated as main events | Confirmed — classified as DOCUMENT with `UNCONFIRMED` |
| Synthesis succeeds or uses controlled batch fallback | Yes — no 413 errors |
| Bad example eliminated: cross-context "2026-03-07: logged in at 2026-03-05T09:12:14" | Must not appear |
| Telemetry logged for any batch failure | Yes |

### 13d. Evidence Linker

| Test | Expected Result |
|:-----|:----------------|
| Links from generic terms alone (INVESTIGATION, VENDOR, EMPLOYEE, etc.) | 0 |
| Specific identifiers (e.g., PO-2026-0342, INV-KBS-0110) create links | Confirmed |
| Every link has provenance | 100% |
| Strength classification matches evidence support | Spot-check ≥3 links |
| No 413 errors | 0 |
| Telemetry logged for any failure | Yes |

### 13e. Subject Analyzer

| Test | Expected Result |
|:-----|:----------------|
| Every subject has valid evidence references | 100% |
| Aliases handled correctly | Confirmed |
| No guilt/culpability claims in output | 0 |
| Uses shared token budget system | Code inspection |
| No 413 errors | 0 |
| Telemetry logged for any failure | Yes |

### 13f. Report Generator

| Test | Expected Result |
|:-----|:----------------|
| 413 errors | 0 |
| Uses compact case state (not raw agent outputs) | Code inspection |
| Findings ranked by evidence support, not insertion order | Compare top 3 against evidence strength |
| No duplicate unresolved questions | 0 |
| DEGRADED notice | Only if upstream agents had genuine, logged failures |
| Report structure | Valid structured JSON matching `InternalReport` schema |
| Telemetry logged for any failure | Yes |

### 13g. Grounding Gate

| Test | Expected Result |
|:-----|:----------------|
| Every saved finding has valid evidence provenance | 100% |
| Every saved timeline event has valid evidence provenance | 100% |
| Every saved connection references valid evidence IDs | 100% |
| Removals/downgrades are never silent | All violations appear in `grounding_violations` with `claim`, `evidence_refs`, `reason`, `action`, `detail` |
| Valid deterministic observations are not removed | Confirmed — `key_statements` with provenance are retained |
| Deterministic observations are not promoted to unsupported findings | Confirmed — observations stay as `key_statements`, not `key_findings` |
| Report not saved as "complete" if gate finds unsupported claims | `generation_status` = `grounding_gate_failed` or `grounding_gate_adjusted` |
| Grounding audit persisted in stored report | `grounding_gate` + `grounding_violations` present in DB document |

### 13h. Telemetry / Failure Logging

| Test | Expected Result |
|:-----|:----------------|
| Every 400 error: classified, logged, retried/reduced, then succeeded or controlled fallback | 0 unhandled or unlogged 400 errors |
| Every 413 error: classified, logged, retried/reduced, then succeeded or controlled fallback | 0 unhandled or unlogged 413 errors |
| 400 remedy uses batch reduction + adaptive output budget, not global max_tokens increase | Code inspection confirms no global max_tokens raise |
| Telemetry includes all required fields | `agent_name`, `evidence_ids`, `batch_number`, `attempt_number`, `model_name`, `error_code`, `error_category`, `estimated_input_tokens`, `requested_output_tokens`, `was_retried`, `retry_action`, `fallback_action` |
| Original provider error is never hidden | `original_message` preserved in `ContextTooLargeError` |
| No API keys or secrets in telemetry | Confirmed by inspection |

### 13i. Overall

| Test | Expected Result |
|:-----|:----------------|
| Total LLM API calls | Record total; compare against the 15–30 benchmark for this dataset, but do not fail the test based on call count |
| Unhandled or unlogged 400/413 errors | **0** — every provider error is classified, telemetry-logged, and handled |
| Evidence quality sacrificed to reduce call count | Never — correctness takes absolute priority |
| API call routing is content-driven | Confirmed — no artificial forcing to hit a number |

---

## 14. Acceptance Criteria

The implementation is accepted when ALL of the following are true:

### Must-Pass (Hard Requirements)

**Evidence integrity**
- [ ] 12/12 files indexed, 73/73 chunks represented
- [ ] 0 unhandled or unlogged 413 errors — every 413 is classified, telemetry-logged, retried/reduced when appropriate, and then either succeeds or uses a controlled fallback
- [ ] 0 unhandled or unlogged 400 errors — every 400 is classified, telemetry-logged, retried/reduced when appropriate, and then either succeeds or uses a controlled fallback
- [ ] 400 remediation uses batch reduction and adaptive output budgets — `max_tokens` is never globally increased as a fix for JSON failures
- [ ] `estimated_input_tokens + system_prompt_tokens + requested_output_tokens` is validated against the model's context limit before every LLM call
- [ ] 0 cross-context date/time combinations in the final timeline
- [ ] 0 generic-word-only links in final connections
- [ ] 0 duplicate timeline events in the final report
- [ ] 0 findings without valid evidence provenance in the final report
- [ ] 0 connections referencing non-existent evidence IDs
- [ ] 0 subjects without evidence references
- [ ] 0 fabricated findings from deterministic extraction (`key_findings = []` invariant enforced)

**Architecture integrity**
- [ ] All 5 downstream agents use the shared token-budget system
- [ ] Context-aware batching preserved (no one-call-per-chunk regression)
- [ ] Token budgets configurable via settings — not hard-coded to a specific TPM value
- [ ] Chunk routing is content-driven — no artificial forcing to meet a call count
- [ ] Deterministic observations (`key_statements`) are never promoted to unsupported findings

**Grounding Gate**
- [ ] Grounding gate runs before every report persistence
- [ ] Every removal or downgrade is recorded in `grounding_violations` with `claim`, `evidence_refs`, `reason`, `action`, `detail`
- [ ] Valid deterministic observations are not removed by the gate
- [ ] Report is not saved with `generation_status = "complete"` if gate finds unsupported claims
- [ ] `grounding_violations` and `grounding_gate` stats are persisted in the DB document

**Telemetry**
- [ ] Every LLM failure (400, 413, 429, timeout) produces a structured telemetry log entry
- [ ] Telemetry includes all required fields: agent, evidence_ids, batch, attempt, model, error_code, category, estimated_tokens, output_tokens, was_retried, retry_action, fallback_action
- [ ] Original provider error message is preserved (never suppressed)
- [ ] No API keys or sensitive credentials in telemetry output

### Should-Pass (Quality Targets — Adaptive, Not Fixed)

- [ ] Record total LLM API calls; compare against the 15–30 benchmark for this dataset, but do not fail the test based on call count
- [ ] No evidence quality or analysis correctness is sacrificed to reduce the API call count
- [ ] Deterministic fallback rate low for LLM-routed chunks on narrative evidence
- [ ] Report generated without DEGRADED notice unless upstream agents had genuine logged failures
- [ ] Findings ranked by evidence support strength, not insertion order
- [ ] Timeline events classified by date type (EVENT / DOCUMENT / REFERENCE / METADATA / AMBIGUOUS)
- [ ] Evidence links scored deterministically before LLM synthesis

> [!IMPORTANT]
> The `<50 calls` figure is a **benchmark for the current 12-file test dataset only**. It is not a hard requirement, a cut-off threshold, or an optimisation target. Evidence quality and correctness take absolute priority. If a future dataset requires 60 successful LLM calls to produce a correctly grounded report, that is the right outcome. The system must never artificially force deterministic routing, reduce batch quality, or skip LLM analysis simply to stay below a call count.
