"""
tests/test_timeline_builder.py
------------------------------
Focused tests for Timeline Builder temporal extraction and validation helpers.
"""

from app.agents.timeline_builder import (
    _build_output,
    _build_timeline_candidates,
    _compute_time_window,
    _parse_date_token,
    _parse_time_token,
    _validate_and_sort_events,
)


def test_timeline_candidates_preserve_provenance_and_compact_snippet():
    evidence_docs = [
        {
            "evidence_id": "E-001",
            "filename": "chat.txt",
            "content": "At 21:14 on 2026-04-12 the payment request was sent.",
        }
    ]
    analyzer_output = {
        "entities_by_evidence": {
            "E-001": {
                "dates": ["2026-04-12"],
                "times": ["21:14"],
                "key_statements": ["Payment request was sent"],
                "key_findings": ["Payment request appears in the chat"],
            }
        }
    }

    candidates = _build_timeline_candidates(evidence_docs, analyzer_output)

    assert candidates
    first = candidates[0]
    assert first["evidence_id"] == "E-001"
    assert first["sort_time"] == "2026-04-12T21:14:00"
    assert first["ambiguous"] is False
    assert first["statements"] == ["Payment request was sent"]
    assert first["provenance"][0]["source"] == "analyzer"
    assert len(first["snippet"]) <= 280


def test_ambiguous_slash_dates_are_flagged_not_guessed():
    parsed = _parse_date_token("04/05/2026")

    assert parsed["date"] is None
    assert parsed["ambiguous"] is True
    assert parsed["ambiguity_reason"] == "slash_date_could_be_dd_mm_or_mm_dd"


def test_unambiguous_slash_date_can_be_normalized():
    parsed = _parse_date_token("14/05/2026")

    assert parsed["ambiguous"] is False
    assert parsed["date"].isoformat() == "2026-05-14T00:00:00"


def test_timezone_offsets_are_normalized_before_sorting():
    time_token = _parse_time_token("21:14+0530")

    assert time_token["timezone"] == "+05:30"

    candidates = [
        {
            "evidence_id": "E-001",
            "sort_time": "2026-04-12T21:14:00+05:30",
            "ambiguous": False,
        },
        {
            "evidence_id": "E-002",
            "sort_time": "2026-04-12T15:00:00Z",
            "ambiguous": False,
        },
    ]
    events = [
        {
            "time": "2026-04-12 21:14 +05:30",
            "sort_time": "2026-04-12T21:14:00+05:30",
            "event": "Later UTC event",
            "refs": ["E-001"],
            "classification": "FACT",
            "confidence": "high",
        },
        {
            "time": "2026-04-12 15:00Z",
            "sort_time": "2026-04-12T15:00:00Z",
            "event": "Earlier UTC event",
            "refs": ["E-002"],
            "classification": "FACT",
            "confidence": "high",
        },
    ]

    sorted_events = _validate_and_sort_events(
        events,
        candidates=candidates,
        valid_evidence_ids={"E-001", "E-002"},
    )

    assert [event["refs"][0] for event in sorted_events] == ["E-002", "E-001"]


def test_validation_rejects_bad_refs_and_downgrades_unproven_sort_time():
    candidates = [
        {
            "evidence_id": "E-001",
            "sort_time": "2026-04-12T21:14:00",
            "ambiguous": False,
        }
    ]
    events = [
        {
            "time": "2026-04-12 21:14",
            "sort_time": "2026-04-12T21:14:00",
            "event": "Accepted event",
            "refs": ["E-001"],
            "classification": "FACT",
            "confidence": "high",
        },
        {
            "time": "2026-04-13 10:00",
            "sort_time": "2026-04-13T10:00:00",
            "event": "Unsupported precise time",
            "refs": ["E-001"],
            "classification": "FACT",
            "confidence": "high",
        },
        {
            "time": "2026-04-12 21:14",
            "sort_time": "2026-04-12T21:14:00",
            "event": "Invalid ref",
            "refs": ["E-999"],
            "classification": "FACT",
            "confidence": "high",
        },
    ]

    validated = _validate_and_sort_events(
        events,
        candidates=candidates,
        valid_evidence_ids={"E-001"},
    )

    assert len(validated) == 2
    assert validated[0]["event"] == "Accepted event"
    assert validated[0]["classification"] == "FACT"
    assert validated[1]["event"] == "Unsupported precise time"
    assert validated[1]["sort_time"] is None
    assert validated[1]["classification"] == "INFERRED"


def test_ambiguous_candidate_stays_unconfirmed_when_llm_invents_sort_time():
    candidates = [
        {
            "evidence_id": "E-002",
            "sort_time": None,
            "ambiguous": True,
        }
    ]
    events = [
        {
            "time": "04/05/2026",
            "sort_time": "2026-04-05T00:00:00",
            "event": "Ambiguous date event",
            "refs": ["E-002"],
            "classification": "FACT",
            "confidence": "high",
        }
    ]

    validated = _validate_and_sort_events(
        events,
        candidates=candidates,
        valid_evidence_ids={"E-002"},
    )

    assert validated[0]["sort_time"] is None
    assert validated[0]["classification"] == "UNCONFIRMED"


def test_output_counts_and_time_window_are_python_computed():
    events = [
        {
            "time": "2026-04-12 21:14",
            "sort_time": "2026-04-12T21:14:00",
            "event": "Fact",
            "refs": ["E-001"],
            "classification": "FACT",
            "confidence": "high",
        },
        {
            "time": "Ambiguous 04/05/2026",
            "sort_time": None,
            "event": "Ambiguous",
            "refs": ["E-002"],
            "classification": "UNCONFIRMED",
            "confidence": "low",
        },
    ]

    time_window = _compute_time_window(events)
    output = _build_output(events, time_window, candidates=[])

    assert output["time_window"] == "2026-04-12 21:14"
    assert output["event_count"] == 2
    assert output["candidate_count"] == 0
    assert output["fact_count"] == 1
    assert output["unconfirmed_count"] == 1


def test_validation_attaches_event_level_provenance():
    candidates = [
        {
            "evidence_id": "E-001",
            "filename": "chat.txt",
            "date_text": "2026-04-12",
            "time_text": "21:14",
            "sort_time": "2026-04-12T21:14:00",
            "ambiguous": False,
            "ambiguity_reason": None,
            "provenance": [
                {
                    "source": "analyzer",
                    "field": "dates",
                    "value": "2026-04-12",
                    "excerpt": "2026-04-12",
                }
            ],
        }
    ]
    events = [
        {
            "time": "2026-04-12 21:14",
            "sort_time": "2026-04-12T21:14:00",
            "event": "Payment request sent",
            "refs": ["E-001"],
            "classification": "FACT",
            "confidence": "high",
        }
    ]

    validated = _validate_and_sort_events(
        events,
        candidates=candidates,
        valid_evidence_ids={"E-001"},
    )

    assert validated[0]["provenance"][0]["evidence_id"] == "E-001"
    assert validated[0]["provenance"][0]["provenance"][0]["source"] == "analyzer"
