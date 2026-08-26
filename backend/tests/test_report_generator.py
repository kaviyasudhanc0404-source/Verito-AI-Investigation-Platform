from app.agents import report_generator
from app.services.report_export_service import export_report


def test_normalise_report_removes_invalid_evidence_ids():
    report = report_generator._normalise_report(
        {
            "executive_summary": "Evidence indicates activity in the available files.",
            "findings": [
                {
                    "text": "Valid finding",
                    "evidence_ids": ["E-001", "E-999"],
                    "confidence": 1.4,
                    "classification": "FACT",
                },
                {"text": "Unsupported finding", "evidence_ids": ["E-999"]},
            ],
            "timeline": [
                {"time": "10:00", "event": "Known event", "refs": ["E-002", "E-999"]},
                {"time": "11:00", "event": "Unsupported event", "refs": ["E-999"]},
            ],
            "connections": [
                {"a": "E-001", "b": "E-002", "reason": "Shared timestamp"},
                {"a": "E-001", "b": "E-999", "reason": "Invalid"},
            ],
            "persons": [
                {"name": "Alex", "note": "Appears in the evidence", "evidence_ids": ["E-001"]},
                {"name": "Casey", "note": "Unsupported", "evidence_ids": ["E-999"]},
            ],
            "unresolved": ["Need original device image"],
        },
        {"E-001", "E-002"},
    )

    assert [finding.evidence_ids for finding in report.findings] == [["E-001"]]
    assert [event.refs for event in report.timeline] == [["E-002"]]
    assert [(link.a, link.b) for link in report.connections] == [("E-001", "E-002")]
    assert [person.name for person in report.persons] == ["Alex"]
    assert report.findings[0].confidence == 1.0


def test_grounding_falls_back_when_findings_are_unsupported():
    fallback = {
        "executive_summary": "Fallback summary.",
        "findings": [
            {
                "text": "Fallback evidence-backed finding",
                "evidence_ids": ["E-001"],
                "confidence": 0.75,
                "classification": "FACT",
            }
        ],
        "timeline": [],
        "connections": [],
        "persons": [],
        "unresolved": ["Manual review may be required."],
    }
    unsupported = report_generator._normalise_report(
        {
            "executive_summary": "Generated summary.",
            "findings": [],
            "timeline": [],
            "connections": [],
            "persons": [],
            "unresolved": [],
        },
        {"E-001"},
    )

    grounded = report_generator._validate_grounding(unsupported, fallback, {"E-001"})

    assert grounded.executive_summary == "Fallback summary."
    assert grounded.findings[0].text == "Fallback evidence-backed finding"


def test_fallback_report_uses_prior_agent_outputs():
    fallback = report_generator._build_fallback_report(
        collector_output={"items_indexed": 2, "items_failed": 1, "evidence_ids": ["E-001", "E-002"]},
        analyzer_output={
            "referenced_key_findings": [
                {"text": "Email mentions a meeting", "evidence_id": "E-001"},
            ],
        },
        timeline_output={
            "events": [
                {
                    "time": "2024-01-01",
                    "event": "Meeting referenced",
                    "refs": ["E-001"],
                    "classification": "FACT",
                    "confidence": "high",
                }
            ],
            "ambiguous_candidate_count": 2,
        },
        linker_output={
            "links": [{"a": "E-001", "b": "E-002", "reason": "Shared contact", "confidence": "medium"}],
            "weak_links": 1,
        },
        subject_output={
            "persons": [
                {
                    "name": "Alex",
                    "note": "Mentioned in the email",
                    "evidence_refs": ["E-001"],
                    "involvement_indicator": "Low",
                }
            ]
        },
        valid_evidence_ids={"E-001", "E-002"},
        investigation_prompt="Review incident communications",
    )

    assert fallback["findings"][0]["evidence_ids"] == ["E-001"]
    assert fallback["timeline"][0]["refs"] == ["E-001"]
    assert fallback["connections"][0]["a"] == "E-001"
    assert fallback["persons"][0]["name"] == "Alex"
    assert len(fallback["unresolved"]) == 3


def test_frontend_report_keeps_schema_and_neutral_language():
    internal = report_generator._normalise_report(
        {
            "executive_summary": "Alex is guilty according to E-001.",
            "findings": [
                {
                    "text": "Alex is guilty",
                    "evidence_ids": ["E-001"],
                    "classification": "INFERRED",
                }
            ],
            "persons": [
                {
                    "name": "Alex",
                    "note": "Alex is the culprit",
                    "evidence_ids": ["E-001"],
                    "involvement_indicator": "High",
                }
            ],
        },
        {"E-001"},
    )
    frontend = report_generator._to_frontend_report(internal)

    assert set(frontend) == {"summary", "findings", "timeline", "connections", "persons", "unresolved"}
    assert "guilty" not in frontend["summary"].lower()
    assert "culprit" not in frontend["persons"][0]["note"].lower()
    assert "[E-001]" in frontend["findings"][0]


def test_json_export_uses_saved_validated_report():
    content, media_type, filename = export_report(
        {
            "investigation_id": "inv_123",
            "report_version": "1.0",
            "generated_at": "2026-08-19T00:00:00+00:00",
            "generation_status": "llm_generated",
            "validated_report": {"executive_summary": "Done", "findings": []},
        },
        "json",
    )

    assert media_type == "application/json"
    assert filename == "verito-report-inv_123.json"
    assert b'"validated_report"' in content
