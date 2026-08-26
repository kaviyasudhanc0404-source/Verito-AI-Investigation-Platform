import json


def test_generic_json_parser_preserves_chat_json_support():
    from app.parsers.json_parser import parse

    chat_data = [
        {"timestamp": "2024-01-15 10:30:00", "sender": "Alice", "message": "Check AUDIT-778"},
    ]

    result = parse(json.dumps(chat_data).encode("utf-8"), "chat_records.json")

    assert "Alice" in result
    assert "Check AUDIT-778" in result
    assert "[JSON Evidence:" not in result


def test_generic_json_parser_flattens_structured_json():
    from app.parsers.json_parser import parse
    from app.services.chunking_service import chunk_text

    audit_data = {
        "system": "erp-prod",
        "events": [
            {
                "timestamp": "2026-03-05T10:02:11+05:30",
                "user": "EMP-1042",
                "action": "vendor_record_updated",
                "vendor_id": "VEN-7781",
            }
        ],
    }

    result = parse(json.dumps(audit_data).encode("utf-8"), "system_audit.json")

    assert "[JSON Evidence: system_audit.json]" in result
    assert "events[0].timestamp: 2026-03-05T10:02:11+05:30" in result
    assert "events[0].vendor_id: VEN-7781" in result
    assert chunk_text(result), "Flattened JSON should be long enough to index"
