"""
tests/test_evidence_linker.py
------------------------------
Focused tests for Evidence Linker candidate construction and validation.
"""

from app.agents.evidence_linker import _build_link_candidates, _validate_links


def test_link_candidates_use_analyzer_and_timeline_support_with_provenance():
    evidence_docs = [
        {"evidence_id": "E-001", "filename": "chat.txt"},
        {"evidence_id": "E-002", "filename": "receipt.pdf"},
    ]
    analyzer_output = {
        "entities_by_evidence": {
            "E-001": {
                "contact_info": ["+91 98765 43210"],
                "locations": ["Chennai"],
            },
            "E-002": {
                "contact_info": ["98765-43210"],
                "locations": ["chennai"],
            },
        }
    }
    timeline_output = {
        "events": [
            {
                "time": "2026-04-12 21:14",
                "sort_time": "2026-04-12T21:14:00",
                "event": "Payment request and receipt line up",
                "refs": ["E-001", "E-002"],
                "classification": "FACT",
                "confidence": "high",
            }
        ]
    }

    candidates = _build_link_candidates(evidence_docs, analyzer_output, timeline_output)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["a"] == "E-001"
    assert candidate["b"] == "E-002"
    assert candidate["suggested_strength"] == "strong"
    assert "shared_contact" in candidate["types"]
    assert "shared_location" in candidate["types"]
    assert "temporal_overlap" in candidate["types"]
    assert candidate["provenance"]
    assert {item["evidence_id"] for item in candidate["provenance"]} == {"E-001", "E-002"}


def test_validation_rejects_invented_links_and_preserves_deterministic_support():
    evidence_docs = [
        {"evidence_id": "E-001", "filename": "chat.txt"},
        {"evidence_id": "E-002", "filename": "receipt.pdf"},
    ]
    analyzer_output = {
        "entities_by_evidence": {
            "E-001": {"contact_info": ["person@example.com"]},
            "E-002": {"contact_info": ["PERSON@example.com"]},
        }
    }

    candidates = _build_link_candidates(evidence_docs, analyzer_output, {})
    links = _validate_links(
        [
            {
                "candidate_id": "not-real",
                "reason": "Invented connection",
                "strength": "strong",
                "confidence": "high",
            },
            {
                "candidate_id": candidates[0]["candidate_id"],
                "reason": "Both evidence items use person@example.com.",
                "strength": "strong",
                "confidence": "high",
            },
        ],
        candidates=candidates,
        valid_evidence_ids={"E-001", "E-002"},
    )

    assert len(links) == 1
    assert links[0]["a"] == "E-001"
    assert links[0]["b"] == "E-002"
    assert links[0]["supports"]
    assert links[0]["provenance"]
    assert links[0]["candidate_id"] == candidates[0]["candidate_id"]
