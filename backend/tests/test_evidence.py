"""
tests/test_evidence.py
-----------------------
Tests for evidence upload, parsing, and utility functions.
Updated to use the new parser modules (parsers/) instead of document_parser.
"""

import pytest
from app.utils.id_generator import format_evidence_id
from app.utils.file_utils import detect_kind
from app.utils.file_detector import detect_file_type


# ── ID Generator Tests ────────────────────────────────────────────────────────

def test_format_evidence_id():
    """Test that evidence IDs are formatted correctly."""
    assert format_evidence_id(1) == "E-001"
    assert format_evidence_id(10) == "E-010"
    assert format_evidence_id(100) == "E-100"


# ── detect_kind (broad category) ──────────────────────────────────────────────

def test_detect_kind_image():
    assert detect_kind("photo.jpg") == "image"
    assert detect_kind("screenshot.PNG") == "image"
    assert detect_kind("photo.webp") == "image"


def test_detect_kind_audio():
    assert detect_kind("recording.mp3") == "audio"
    assert detect_kind("voice.wav") == "audio"
    assert detect_kind("memo.m4a") == "audio"


def test_detect_kind_video():
    assert detect_kind("clip.mp4") == "video"


def test_detect_kind_doc():
    assert detect_kind("report.pdf") == "doc"
    assert detect_kind("notes.docx") == "doc"
    assert detect_kind("log.txt") == "doc"
    assert detect_kind("data.csv") == "doc"


def test_detect_kind_chat():
    assert detect_kind("email.eml") == "chat"


# ── detect_file_type (granular type) ──────────────────────────────────────────

def test_detect_file_type_pdf():
    assert detect_file_type("document.pdf") == "pdf"


def test_detect_file_type_image():
    assert detect_file_type("photo.jpg") == "image"
    assert detect_file_type("scene.PNG") == "image"
    assert detect_file_type("clip.webp") == "image"


def test_detect_file_type_email():
    assert detect_file_type("message.eml") == "email"


def test_detect_file_type_chat():
    assert detect_file_type("chat_export.json") == "chat"
    assert detect_file_type("messages.txt") == "txt"


def test_detect_file_type_csv():
    assert detect_file_type("suspects.csv") == "csv"


def test_detect_file_type_docx():
    assert detect_file_type("report.docx") == "docx"
    assert detect_file_type("notes.doc") == "doc"


# ── New parser smoke tests ─────────────────────────────────────────────────────

def test_parse_txt_via_new_parser():
    """Test plain text extraction using chat_parser (txt fallback)."""
    from app.parsers.chat_parser import parse
    content = b"Alice: Hello\nBob: World"
    result = parse(content, "chat.txt")
    assert "Alice" in result
    assert "Hello" in result


def test_parse_csv_via_new_parser():
    """Test CSV to readable text conversion."""
    from app.parsers.csv_parser import parse
    csv_data = b"Name,Date,Amount\nJohn Doe,2024-01-01,500\nJane Smith,2024-01-02,300"
    result = parse(csv_data, "data.csv")
    assert "Name:" in result or "John Doe" in result
    assert "John Doe" in result
    assert "Jane Smith" in result


def test_parse_eml_via_new_parser():
    """Test EML email parsing using the new email_parser."""
    from app.parsers.email_parser import parse
    eml_data = (
        b"From: sender@example.com\r\n"
        b"To: receiver@example.com\r\n"
        b"Subject: Test Email\r\n"
        b"Date: Mon, 1 Jan 2024 12:00:00 +0000\r\n"
        b"Content-Type: text/plain\r\n\r\n"
        b"This is the email body."
    )
    result = parse(eml_data, "message.eml")
    assert "sender@example.com" in result
    assert "Test Email" in result
    assert "This is the email body." in result


# ── Auth protection test ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_requires_auth():
    """Test that evidence upload requires authentication."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/evidence/upload/inv_test123",
            files={"files": ("test.txt", b"test content", "text/plain")},
        )
    assert response.status_code in (401, 403)
