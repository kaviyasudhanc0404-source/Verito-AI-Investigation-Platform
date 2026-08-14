"""
tests/test_subject_analyzer.py
------------------------------
Focused tests for Subject Analyzer candidate building, deterministic scoring,
filtered context, and validation.
"""

from app.agents.subject_analyzer import (
    _build_output,
    _build_subject_candidates,
    _subject_key,
    _validate_persons,
)


def test_subject_key_is_conservative_about_identity_resolution():
    assert _subject_key("John Smith") == _subject_key("Mr. John Smith")
    assert _subject_key("John Smith") != _subject_key("J. Smith")
    assert _subject_key("John") != _subject_key("John Smith")


def test_subject_candidates_merge_title_variant_and_preserve_provenance():
    evidence_docs = [
        {
            "evidence_id": "E-001",
            "filename": "chat.txt",
            "content": "John Smith met Alice in Mumbai. Mr. John Smith shared john@example.com.",
        },
        {
            "evidence_id": "E-002",
            "filename": "email.txt",
            "content": "Mr. John Smith is listed with ABC Ltd in Delhi.",
        },
    ]
    analyzer_output = {
        "entities_by_evidence": {
            "E-001": {
                "filename": "chat.txt",
                "people": ["John Smith", "Alice"],
                "locations": ["Mumbai"],
                "organisations": [],
                "contact_info": ["john@example.com"],
                "dates": [],
                "times": [],
                "key_statements": ["John Smith met Alice in Mumbai"],
                "key_findings": ["John Smith shared john@example.com"],
            },
            "E-002": {
                "filename": "email.txt",
                "people": ["Mr. John Smith"],
                "locations": ["Delhi"],
                "organisations": ["ABC Ltd"],
                "contact_info": [],
                "dates": [],
                "times": [],
                "key_statements": ["Mr. John Smith is listed with ABC Ltd"],
                "key_findings": [],
            },
        }
    }

    candidates = _build_subject_candidates(
        evidence_docs=evidence_docs,
        analyzer_output=analyzer_output,
        timeline_output={},
        linker_output={},
    )
    john = next(candidate for candidate in candidates if candidate["name"] == "John Smith")

    assert john["evidence_refs"] == ["E-001", "E-002"]
    assert john["locations"] == ["Mumbai", "Delhi"]
    assert john["organisations"] == ["ABC Ltd"]
    assert john["contact_info"] == ["john@example.com"]
    assert john["involvement_indicator"] == "High"
    assert john["confidence"] == "high"
    assert len(john["provenance"]) == 2


def test_timeline_and_linker_context_is_filtered_to_subject_relevance():
    evidence_docs = [
        {
            "evidence_id": "E-001",
            "filename": "chat.txt",
            "content": "John Smith sent the message.",
        },
        {
            "evidence_id": "E-002",
            "filename": "bank.txt",
            "content": "Transfer record without subject name.",
        },
    ]
    analyzer_output = {
        "entities_by_evidence": {
            "E-001": {
                "filename": "chat.txt",
                "people": ["John Smith"],
                "locations": [],
                "organisations": [],
                "contact_info": [],
                "dates": [],
                "times": [],
                "key_statements": ["John Smith sent the message"],
                "key_findings": [],
            },
        }
    }
    timeline_output = {
        "events": [
            {
                "time": "2026-04-12",
                "sort_time": "2026-04-12T00:00:00",
                "event": "John Smith sent the message",
                "refs": ["E-001"],
                "classification": "FACT",
            },
            {
                "time": "2026-04-13",
                "sort_time": "2026-04-13T00:00:00",
                "event": "Unrelated transfer recorded",
                "refs": ["E-001", "E-002"],
                "classification": "FACT",
            },
        ]
    }
    linker_output = {
        "links": [
            {
                "candidate_id": "L-001",
                "a": "E-001",
                "b": "E-002",
                "reason": "John Smith appears in the chat and supporting record.",
                "strength": "moderate",
                "types": ["shared_person"],
                "supports": [],
            },
            {
                "candidate_id": "L-002",
                "a": "E-001",
                "b": "E-002",
                "reason": "Unrelated date overlap.",
                "strength": "weak",
                "types": ["shared_date"],
                "supports": [],
            },
        ]
    }

    candidates = _build_subject_candidates(
        evidence_docs=evidence_docs,
        analyzer_output=analyzer_output,
        timeline_output=timeline_output,
        linker_output=linker_output,
    )

    john = candidates[0]
    assert [event["event"] for event in john["timeline_refs"]] == [
        "John Smith sent the message"
    ]
    assert [link["link_id"] for link in john["link_refs"]] == ["L-001"]


def test_validation_uses_python_confidence_and_rejects_guilt_language():
    candidates = [
        {
            "candidate_id": "S-001",
            "name": "John Smith",
            "names": ["John Smith"],
            "mention_count": 3,
            "locations": ["Mumbai"],
            "organisations": [],
            "contact_info": [],
            "evidence_refs": ["E-001"],
            "timeline_refs": [],
            "link_refs": [],
            "involvement_indicator": "Moderate",
            "confidence": "medium",
            "score": 4.0,
            "note_hint": "John Smith is represented in one evidence item.",
            "provenance": [
                {
                    "evidence_id": "E-001",
                    "filename": "chat.txt",
                    "field": "people",
                    "value": "John Smith",
                }
            ],
        }
    ]
    llm_persons = [
        {
            "candidate_id": "S-001",
            "note": "John Smith is guilty based on the chat.",
            "confidence": "high",
            "involvement_indicator": "High",
            "evidence_refs": ["E-999"],
        }
    ]

    persons = _validate_persons(
        llm_persons,
        candidates=candidates,
        valid_evidence_ids={"E-001"},
    )

    assert persons[0]["note"] == "John Smith is represented in one evidence item."
    assert persons[0]["confidence"] == "medium"
    assert persons[0]["involvement_indicator"] == "Moderate"
    assert persons[0]["evidence_refs"] == ["E-001"]


def test_output_counts_are_computed_from_validated_persons():
    persons = [
        {"involvement_indicator": "High"},
        {"involvement_indicator": "Moderate"},
        {"involvement_indicator": "Low"},
    ]

    output = _build_output(persons, candidates=[])

    assert output["subject_count"] == 3
    assert output["high_involvement_count"] == 1
    assert output["moderate_involvement_count"] == 1
    assert output["low_involvement_count"] == 1
