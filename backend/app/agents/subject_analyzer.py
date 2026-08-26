"""
agents/subject_analyzer.py
---------------------------
Agent 05 - Subject Analyzer

Profiles persons represented in the evidence. The agent measures evidence
representation only; it never estimates guilt, culpability, or blame.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Awaitable, Callable

from app.database import collections
from app.services.llm_service import chat_completion_json
from app.utils.datetime_utils import utcnow

Emitter = Callable[[str, dict], Awaitable[None]]

_SYSTEM_PROMPT = """You are a forensic subject analyst.
Write concise subject profile notes using ONLY the provided subject candidates.

Core rule:
- Answer how strongly each person is represented in the evidence.
- Never answer or imply how guilty, suspicious, liable, or culpable a person is.

Rules:
- Use only candidate_id values from the provided candidates.
- Do not invent people, aliases, evidence IDs, locations, organisations,
  contacts, links, or timeline events.
- Do not change involvement indicators or confidence values; those are computed by the system.
- A note must describe evidence presence, not blame.
- Return ONLY valid JSON in the requested schema.

Return JSON:
{
  "persons": [
    {
      "candidate_id": "S-001",
      "note": "brief evidence-grounded description of this person's representation"
    }
  ]
}"""

_MAX_CANDIDATES_FOR_LLM = 60
_MAX_TEXT_SNIPPET_CHARS = 220
_MAX_CONTEXT_ITEMS = 8
_TITLE_RE = re.compile(r"^(mr|mrs|ms|miss|dr|prof|sir|madam)\.?\s+", re.IGNORECASE)
_NAME_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_RISK_WORDS_RE = re.compile(
    r"\b(guilt|guilty|culpable|culpability|liable|liability|criminal|suspect score|"
    r"blame|blamed|perpetrator|offender)\b",
    re.IGNORECASE,
)


async def run(
    investigation_id: str,
    emit: Emitter,
    analyzer_output: dict,
    timeline_output: dict | None = None,
    linker_output: dict | None = None,
) -> dict:
    """Run the Subject Analyzer agent."""
    agent_id = "subject"
    await _set_status(investigation_id, agent_id, "running")
    lines: list[dict] = []

    async def add_line(kind: str, text: str) -> None:
        line = {"kind": kind, "text": text}
        lines.append(line)
        await _append_line(investigation_id, agent_id, line)
        await emit("agent_activity", {"agent_id": agent_id, "line": line})

    await add_line("scan", "Building deterministic subject candidates...")

    evidence_docs = await collections.evidence().find(
        {"investigation_id": investigation_id}
    ).to_list(length=None)
    valid_evidence_ids = {
        str(ev.get("evidence_id") or "") for ev in evidence_docs if ev.get("evidence_id")
    }

    candidates = _build_subject_candidates(
        evidence_docs=evidence_docs,
        analyzer_output=analyzer_output,
        timeline_output=timeline_output or {},
        linker_output=linker_output or {},
    )

    await add_line(
        "info",
        f"Found {len(candidates)} subject candidate(s) with evidence support.",
    )

    if not candidates:
        output = _empty_output()
        await add_line("warn", "No persons identified in the evidence.")
        await _complete(investigation_id, agent_id, lines, ["No subjects identified"], output)
        await emit("agent_completed", {"agent_id": agent_id})
        return output

    compact_candidates = _compact_candidates_for_llm(candidates)
    try:
        await add_line("scan", "Synthesising subject notes from filtered context...")
        synthesis = await chat_completion_json(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Subject candidates with deterministic scoring:\n"
                        f"{json.dumps(compact_candidates, separators=(',', ':'))}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=2500,
        )
    except Exception as exc:
        await add_line("warn", f"Subject note synthesis failed: {str(exc)[:80]}")
        synthesis = _fallback_synthesis(candidates)

    persons = _validate_persons(
        synthesis.get("persons", []),
        candidates=candidates,
        valid_evidence_ids=valid_evidence_ids,
    )
    output = _build_output(persons, candidates)

    await asyncio.sleep(0.2)

    for person in persons[:12]:
        await add_line(
            "ok",
            (
                f"{person['name']} - {person['mentions']} mention(s), "
                f"{len(person['evidence_refs'])} evidence ref(s) "
                f"[Representation: {person['involvement_indicator']}]"
            ),
        )

    if len(persons) > 12:
        await add_line("info", f"{len(persons) - 12} additional profile(s) saved.")

    await add_line(
        "ok",
        (
            f"{len(persons)} subject profile(s) validated: "
            f"{output['high_involvement_count']} high, "
            f"{output['moderate_involvement_count']} moderate, "
            f"{output['low_involvement_count']} low representation."
        ),
    )

    summary = [
        f"{len(persons)} person(s) represented",
        f"{output['high_involvement_count']} high representation",
    ]

    await _complete(investigation_id, agent_id, lines, summary, output)
    await emit("agent_completed", {"agent_id": agent_id})
    return output


def _empty_output() -> dict:
    return {
        "persons": [],
        "subject_count": 0,
        "high_involvement_count": 0,
        "moderate_involvement_count": 0,
        "low_involvement_count": 0,
        "candidate_count": 0,
        "subject_candidates": [],
    }


def _build_subject_candidates(
    *,
    evidence_docs: list[dict],
    analyzer_output: dict,
    timeline_output: dict,
    linker_output: dict,
) -> list[dict]:
    buckets = analyzer_output.get("entities_by_evidence", {})
    if not isinstance(buckets, dict):
        buckets = {}

    docs_by_id = {
        str(ev.get("evidence_id") or ""): ev
        for ev in evidence_docs
        if ev.get("evidence_id")
    }
    grouped: dict[str, dict] = {}

    for evidence_id, bucket in buckets.items():
        evidence_id = str(evidence_id)
        if not isinstance(bucket, dict):
            continue

        filename = _normalise_text(bucket.get("filename")) or _doc_filename(
            docs_by_id, evidence_id
        )
        content = _normalise_text(docs_by_id.get(evidence_id, {}).get("content", ""))
        people = _clean_list(bucket.get("people", []))

        for raw_name in people:
            key = _subject_key(raw_name)
            if not key:
                continue

            candidate = grouped.setdefault(
                key,
                {
                    "canonical_key": key,
                    "names": [],
                    "evidence_refs": [],
                    "locations": [],
                    "organisations": [],
                    "contact_info": [],
                    "statements": [],
                    "findings": [],
                    "mention_count": 0,
                    "provenance": [],
                },
            )

            _add_unique(candidate["names"], _display_name(raw_name))
            _add_unique(candidate["evidence_refs"], evidence_id)
            _extend_unique(candidate["locations"], _clean_list(bucket.get("locations", [])))
            _extend_unique(
                candidate["organisations"], _clean_list(bucket.get("organisations", []))
            )
            _extend_unique(
                candidate["contact_info"], _clean_list(bucket.get("contact_info", []))
            )
            _extend_unique(
                candidate["statements"],
                _subject_relevant_texts(raw_name, bucket.get("key_statements", [])),
            )
            _extend_unique(
                candidate["findings"],
                _subject_relevant_texts(raw_name, bucket.get("key_findings", [])),
            )
            candidate["mention_count"] += _count_name_mentions(raw_name, content) or 1
            candidate["provenance"].append(
                {
                    "evidence_id": evidence_id,
                    "filename": filename,
                    "field": "people",
                    "value": raw_name,
                    "excerpt": _subject_excerpt(raw_name, content),
                }
            )

    timeline_refs = _timeline_refs_by_subject(grouped, timeline_output)
    link_refs = _link_refs_by_subject(grouped, linker_output)

    candidates = []
    for index, candidate in enumerate(grouped.values(), start=1):
        candidate["name"] = _choose_canonical_name(candidate["names"])
        candidate["evidence_refs"] = sorted(candidate["evidence_refs"])
        candidate["timeline_refs"] = timeline_refs.get(candidate["canonical_key"], [])
        candidate["link_refs"] = link_refs.get(candidate["canonical_key"], [])
        candidate["score"] = _score_candidate(candidate)
        candidate["involvement_indicator"] = _involvement_from_candidate(candidate)
        candidate["confidence"] = _confidence_from_candidate(candidate)
        candidate["candidate_id"] = f"S-{index:03d}"
        candidate["note_hint"] = _note_hint(candidate)
        candidates.append(candidate)

    return sorted(
        candidates,
        key=lambda item: (-item["score"], item["name"].casefold(), item["candidate_id"]),
    )


def _timeline_refs_by_subject(
    grouped: dict[str, dict],
    timeline_output: dict,
) -> dict[str, list[dict]]:
    events = timeline_output.get("events", [])
    if not isinstance(events, list):
        return {}

    refs_by_subject = {}
    for key, candidate in grouped.items():
        subject_refs = set(candidate.get("evidence_refs", []))
        matches = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_refs = set(_clean_list(event.get("refs", [])))
            if not subject_refs.intersection(event_refs):
                continue
            event_text = _normalise_text(event.get("event"))
            if not _text_mentions_subject(event_text, candidate["names"]):
                continue
            matches.append(
                {
                    "time": _normalise_text(event.get("time")) or "Unknown",
                    "sort_time": _normalise_text(event.get("sort_time")) or None,
                    "event": event_text,
                    "refs": sorted(subject_refs.intersection(event_refs)),
                    "classification": _normalise_text(event.get("classification"))
                    or "UNCONFIRMED",
                }
            )
        refs_by_subject[key] = matches[:_MAX_CONTEXT_ITEMS]
    return refs_by_subject


def _link_refs_by_subject(
    grouped: dict[str, dict],
    linker_output: dict,
) -> dict[str, list[dict]]:
    links = linker_output.get("links", [])
    if not isinstance(links, list):
        return {}

    refs_by_subject = {}
    for key, candidate in grouped.items():
        subject_refs = set(candidate.get("evidence_refs", []))
        matches = []
        for link in links:
            if not isinstance(link, dict):
                continue
            a = _normalise_text(link.get("a"))
            b = _normalise_text(link.get("b"))
            if not subject_refs.intersection({a, b}):
                continue
            reason = _normalise_text(link.get("reason"))
            support_text = " ".join(
                [
                    reason,
                    json.dumps(link.get("supports", []), separators=(",", ":"))[:800],
                ]
            )
            if not _text_mentions_subject(support_text, candidate["names"]):
                continue
            matches.append(
                {
                    "link_id": _normalise_text(link.get("candidate_id")) or None,
                    "a": a,
                    "b": b,
                    "reason": reason,
                    "strength": _normalise_text(link.get("strength")) or "weak",
                    "types": _clean_list(link.get("types", [])),
                }
            )
        refs_by_subject[key] = matches[:_MAX_CONTEXT_ITEMS]
    return refs_by_subject


def _compact_candidates_for_llm(candidates: list[dict]) -> list[dict]:
    compact = []
    for candidate in candidates[:_MAX_CANDIDATES_FOR_LLM]:
        compact.append(
            {
                "candidate_id": candidate["candidate_id"],
                "name": candidate["name"],
                "known_name_variants": candidate["names"],
                "mentions": candidate["mention_count"],
                "evidence_refs": candidate["evidence_refs"],
                "locations": candidate["locations"][:_MAX_CONTEXT_ITEMS],
                "organisations": candidate["organisations"][:_MAX_CONTEXT_ITEMS],
                "contact_info": candidate["contact_info"][:_MAX_CONTEXT_ITEMS],
                "timeline_refs": candidate["timeline_refs"],
                "link_refs": candidate["link_refs"],
                "involvement_indicator": candidate["involvement_indicator"],
                "confidence": candidate["confidence"],
                "note_hint": candidate["note_hint"],
                "provenance": candidate["provenance"][:_MAX_CONTEXT_ITEMS],
            }
        )
    return compact


def _fallback_synthesis(candidates: list[dict]) -> dict:
    return {
        "persons": [
            {"candidate_id": candidate["candidate_id"], "note": candidate["note_hint"]}
            for candidate in candidates[:_MAX_CANDIDATES_FOR_LLM]
        ]
    }


def _validate_persons(
    persons: object,
    *,
    candidates: list[dict],
    valid_evidence_ids: set[str],
    allow_fallback: bool = True,
) -> list[dict]:
    if not isinstance(persons, list):
        persons = []

    candidate_by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    cleaned = []
    seen_ids = set()

    for item in persons:
        if not isinstance(item, dict):
            continue

        candidate = candidate_by_id.get(str(item.get("candidate_id") or ""))
        if not candidate or candidate["candidate_id"] in seen_ids:
            continue
        seen_ids.add(candidate["candidate_id"])

        evidence_refs = [
            ref for ref in candidate["evidence_refs"] if ref in valid_evidence_ids
        ]
        if not evidence_refs:
            continue

        note = _safe_note(item.get("note"), candidate["note_hint"])
        cleaned.append(
            {
                "name": candidate["name"],
                "mentions": candidate["mention_count"],
                "locations": candidate["locations"],
                "organisations": candidate["organisations"],
                "contact_info": candidate["contact_info"],
                "evidence_refs": evidence_refs,
                "timeline_refs": candidate["timeline_refs"],
                "link_refs": candidate["link_refs"],
                "involvement_indicator": candidate["involvement_indicator"],
                "confidence": candidate["confidence"],
                "representation_score": candidate["score"],
                "note": note,
                "provenance": [
                    item
                    for item in candidate["provenance"]
                    if item.get("evidence_id") in valid_evidence_ids
                ],
                "candidate_id": candidate["candidate_id"],
            }
        )

    if cleaned and allow_fallback:
        cleaned_ids = {person["candidate_id"] for person in cleaned}
        missing = [
            {"candidate_id": candidate["candidate_id"], "note": candidate["note_hint"]}
            for candidate in candidates
            if candidate["candidate_id"] not in cleaned_ids
        ]
        if missing:
            cleaned.extend(
                _validate_persons(
                    missing,
                    candidates=candidates,
                    valid_evidence_ids=valid_evidence_ids,
                    allow_fallback=False,
                )
            )

    if cleaned or not allow_fallback:
        return sorted(
            cleaned,
            key=lambda item: (
                -item["representation_score"],
                item["name"].casefold(),
            ),
        )

    return _validate_persons(
        _fallback_synthesis(candidates).get("persons", []),
        candidates=candidates,
        valid_evidence_ids=valid_evidence_ids,
        allow_fallback=False,
    )


def _build_output(persons: list[dict], candidates: list[dict]) -> dict:
    return {
        "persons": persons,
        "subject_count": len(persons),
        "high_involvement_count": sum(
            1 for person in persons if person["involvement_indicator"] == "High"
        ),
        "moderate_involvement_count": sum(
            1 for person in persons if person["involvement_indicator"] == "Moderate"
        ),
        "low_involvement_count": sum(
            1 for person in persons if person["involvement_indicator"] == "Low"
        ),
        "candidate_count": len(candidates),
        "subject_candidates": _compact_candidates_for_llm(candidates),
    }


def _score_candidate(candidate: dict) -> float:
    evidence_count = len(candidate.get("evidence_refs", []))
    score = evidence_count * 2.0
    score += min(candidate.get("mention_count", 0), 8) * 0.25
    score += min(len(candidate.get("locations", [])), 4) * 0.75
    score += min(len(candidate.get("organisations", [])), 4) * 1.0
    score += min(len(candidate.get("contact_info", [])), 4) * 1.25
    score += min(len(candidate.get("timeline_refs", [])), 4) * 1.0
    score += min(len(candidate.get("link_refs", [])), 4) * 1.5
    score += min(
        len(candidate.get("statements", [])) + len(candidate.get("findings", [])), 6
    ) * 0.5
    return round(score, 2)


def _involvement_from_candidate(candidate: dict) -> str:
    evidence_count = len(candidate.get("evidence_refs", []))
    score = float(candidate.get("score", 0))
    has_strong_context = bool(candidate.get("contact_info") or candidate.get("link_refs"))

    if (
        evidence_count >= 3
        or score >= 7.0
        or (evidence_count >= 2 and has_strong_context)
    ):
        return "High"
    if evidence_count >= 2 or score >= 3.75:
        return "Moderate"
    return "Low"


def _confidence_from_candidate(candidate: dict) -> str:
    evidence_count = len(candidate.get("evidence_refs", []))
    has_context = any(
        candidate.get(key)
        for key in (
            "locations",
            "organisations",
            "contact_info",
            "timeline_refs",
            "link_refs",
        )
    )

    if evidence_count >= 2 and has_context:
        return "high"
    if evidence_count >= 1 and (has_context or candidate.get("mention_count", 0) >= 2):
        return "medium"
    return "low"


def _note_hint(candidate: dict) -> str:
    parts = [
        (
            f"{candidate['name']} is represented in {len(candidate['evidence_refs'])} "
            f"evidence item(s) with {candidate['mention_count']} mention(s)."
        )
    ]
    if candidate.get("contact_info"):
        parts.append("Contact details appear in the same evidence context.")
    if candidate.get("organisations"):
        parts.append("Organisation context is present.")
    if candidate.get("locations"):
        parts.append("Location context is present.")
    if candidate.get("timeline_refs"):
        parts.append("Subject-relevant timeline context is present.")
    if candidate.get("link_refs"):
        parts.append("Subject-relevant evidence links are present.")
    return " ".join(parts)


def _safe_note(value: object, fallback: str) -> str:
    note = _normalise_text(value)
    if not note or _RISK_WORDS_RE.search(note):
        return fallback
    return note


def _subject_relevant_texts(name: str, values: object) -> list[str]:
    names = [name, _strip_title(name)]
    return [
        text[:_MAX_TEXT_SNIPPET_CHARS]
        for text in _clean_list(values)
        if _text_mentions_subject(text, names)
    ]


def _text_mentions_subject(text: object, names: list[str]) -> bool:
    haystack = _normalise_text(text)
    if not haystack:
        return False
    return any(_count_name_mentions(name, haystack) > 0 for name in names)


def _count_name_mentions(name: str, text: str) -> int:
    clean_name = _normalise_text(name)
    clean_text = _normalise_text(text)
    if not clean_name or not clean_text:
        return 0

    variants = {clean_name, _strip_title(clean_name)}
    count = 0
    for variant in variants:
        if not variant:
            continue
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        count = max(count, len(pattern.findall(clean_text)))
    return count


def _subject_excerpt(name: str, text: str) -> str:
    clean_text = _normalise_text(text)
    if not clean_text:
        return ""

    variants = [name, _strip_title(name)]
    for variant in variants:
        if not variant:
            continue
        match = re.search(re.escape(variant), clean_text, re.IGNORECASE)
        if match:
            left = max(0, match.start() - 80)
            right = min(len(clean_text), match.end() + 80)
            return clean_text[left:right][:_MAX_TEXT_SNIPPET_CHARS]
    return clean_text[:_MAX_TEXT_SNIPPET_CHARS]


def _subject_key(name: object) -> str:
    text = _strip_title(_normalise_text(name))
    if not text:
        return ""

    tokens = _NAME_TOKEN_RE.findall(text.casefold())
    if not tokens:
        return ""

    if len(tokens) == 1:
        return f"single:{tokens[0]}"

    if any(len(token) == 1 for token in tokens):
        return "initials:" + " ".join(tokens)

    return "full:" + " ".join(tokens)


def _strip_title(name: object) -> str:
    return _TITLE_RE.sub("", _normalise_text(name)).strip()


def _display_name(name: object) -> str:
    return _normalise_text(name).strip(" ,.;:")


def _choose_canonical_name(names: list[str]) -> str:
    if not names:
        return "Unknown"
    cleaned_names = [_strip_title(name) or name for name in names]
    return sorted(
        cleaned_names,
        key=lambda item: (-len(_NAME_TOKEN_RE.findall(item)), len(item), item),
    )[0]


def _doc_filename(docs_by_id: dict[str, dict], evidence_id: str) -> str:
    return _normalise_text(docs_by_id.get(evidence_id, {}).get("filename")) or "unknown"


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        _add_unique(target, value)


def _add_unique(target: list[str], value: object) -> bool:
    text = _normalise_text(value)
    if not text:
        return False
    existing = {item.casefold() for item in target}
    if text.casefold() in existing:
        return False
    target.append(text)
    return True


def _clean_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []

    cleaned = []
    seen = set()
    for value in values:
        text = _normalise_text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned


def _normalise_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


async def _set_status(investigation_id: str, agent_id: str, status: str) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {"$set": {"status": status, "updated_at": utcnow()}},
        upsert=True,
    )


async def _append_line(investigation_id: str, agent_id: str, line: dict) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {"$push": {"lines": line}, "$set": {"updated_at": utcnow()}},
    )


async def _complete(
    investigation_id: str,
    agent_id: str,
    lines: list[dict],
    summary: list[str],
    output: dict,
) -> None:
    await collections.agent_runs().update_one(
        {"investigation_id": investigation_id, "agent_id": agent_id},
        {
            "$set": {
                "status": "done",
                "summary": summary,
                "output": output,
                "updated_at": utcnow(),
            }
        },
    )
