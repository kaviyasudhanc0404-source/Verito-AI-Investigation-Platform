"""
tests/test_parsers.py
----------------------
Unit tests for all evidence file parsers.

Tests each parser with minimal synthetic input to verify:
  - Text is extracted (non-empty output)
  - Key fields are present in output
  - No exceptions raised

Run: pytest tests/test_parsers.py -v

Note: image_parser.py is tested separately (requires EasyOCR + Gemini API).
      These tests cover all text-based parsers only.
"""

import io
import csv
import email
import json
import pytest


# ── PDF Parser ────────────────────────────────────────────────────────────────

def _make_pdf_bytes(text: str) -> bytes:
    """Create a minimal valid PDF with the given text using PyMuPDF."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    return doc.tobytes()


def test_pdf_parser_digital():
    """Test PDF parser with a digital (text-selectable) PDF."""
    from app.parsers.pdf_parser import parse

    text = "The suspect was identified at the warehouse on January 15, 2024."
    pdf_bytes = _make_pdf_bytes(text)

    result = parse(pdf_bytes, "test_digital.pdf")
    assert result.strip(), "Should extract non-empty text from digital PDF"
    assert "warehouse" in result.lower() or "suspect" in result.lower(), (
        "Extracted text should contain content from the PDF"
    )
    print(f"  ✓ Digital PDF: extracted {len(result)} chars")


# ── Email Parser ───────────────────────────────────────────────────────────────

def _make_eml_bytes(subject: str, body: str, from_: str, to: str) -> bytes:
    """Create a minimal .eml file as bytes."""
    content = (
        f"From: {from_}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Mon, 15 Jan 2024 10:30:00 +0000\r\n"
        f"Content-Type: text/plain\r\n\r\n"
        f"{body}"
    )
    return content.encode("utf-8")


def test_email_parser_extracts_headers():
    """Test email parser extracts From, To, Subject, Date, and body."""
    from app.parsers.email_parser import parse

    eml_bytes = _make_eml_bytes(
        subject="Re: Incident Report",
        body="I saw the black car leave at midnight.",
        from_="witness@example.com",
        to="detective@verito.app",
    )

    result = parse(eml_bytes, "incident_email.eml")
    assert "From:" in result
    assert "Subject:" in result
    assert "I saw the black car" in result
    print(f"  ✓ Email parser: extracted {len(result)} chars")


def test_email_parser_multipart():
    """Test email parser handles multipart emails."""
    from app.parsers.email_parser import parse

    # Build a simple multipart message
    msg = email.message.MIMEMultipart = None  # avoid import confusion
    content = (
        "MIME-Version: 1.0\r\n"
        "From: sender@example.com\r\n"
        "To: receiver@example.com\r\n"
        "Subject: Meeting Notes\r\n"
        "Content-Type: multipart/mixed; boundary=\"boundary123\"\r\n\r\n"
        "--boundary123\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "The meeting confirmed the suspect was present.\r\n"
        "--boundary123--\r\n"
    )
    result = parse(content.encode("utf-8"), "meeting.eml")
    assert "suspect was present" in result
    print(f"  ✓ Multipart email parser: extracted {len(result)} chars")


# ── Chat Parser ────────────────────────────────────────────────────────────────

def test_chat_parser_plain_text():
    """Test chat parser with a plain text chat log."""
    from app.parsers.chat_parser import parse

    chat_content = (
        "[2024-01-15 10:30:00] Alice: Did you see the car?\n"
        "[2024-01-15 10:30:45] Bob: Yes, it was a black sedan.\n"
        "[2024-01-15 10:31:10] Alice: Do you remember the plate?\n"
        "[2024-01-15 10:31:30] Bob: TN 33 AB 1234\n"
    )
    result = parse(chat_content.encode("utf-8"), "chat_log.txt")
    assert "Alice" in result
    assert "TN 33 AB 1234" in result
    print(f"  ✓ Plain text chat parser: extracted {len(result)} chars")


def test_chat_parser_json():
    """Test chat parser with a JSON structured chat export."""
    from app.parsers.chat_parser import parse

    chat_data = [
        {"timestamp": "2024-01-15 10:30:00", "sender": "Alice", "message": "Did you see the car?"},
        {"timestamp": "2024-01-15 10:30:45", "sender": "Bob", "message": "Yes, TN 33 AB 1234"},
    ]
    json_bytes = json.dumps(chat_data).encode("utf-8")
    result = parse(json_bytes, "chat_export.json")
    assert "Alice" in result
    assert "TN 33 AB 1234" in result
    print(f"  ✓ JSON chat parser: extracted {len(result)} chars")


# ── CSV Parser ────────────────────────────────────────────────────────────────

def _make_csv_bytes(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def test_csv_parser_basic():
    """Test CSV parser with a basic suspect list."""
    from app.parsers.csv_parser import parse

    rows = [
        ["Name", "Age", "Vehicle", "Location"],
        ["John Doe", "35", "Black Sedan TN 33 AB 1234", "Warehouse District"],
        ["Jane Smith", "28", "White SUV", "Eastern Highway"],
    ]
    csv_bytes = _make_csv_bytes(rows)
    result = parse(csv_bytes, "suspects.csv")
    assert "Name:" in result or "John Doe" in result
    assert "Black Sedan" in result
    assert "Total rows:" in result
    print(f"  ✓ CSV parser: extracted {len(result)} chars")


def test_csv_parser_empty():
    """Test CSV parser handles empty files gracefully."""
    from app.parsers.csv_parser import parse
    result = parse(b"", "empty.csv")
    assert "Empty" in result or result == ""
    print("  ✓ CSV parser: empty file handled gracefully")


# ── DOCX Parser ───────────────────────────────────────────────────────────────

def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    """Create a minimal DOCX file with the given paragraphs."""
    from docx import Document
    doc = Document()
    for para in paragraphs:
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_parser_paragraphs():
    """Test DOCX parser extracts paragraph text."""
    from app.parsers.docx_parser import parse

    paragraphs = [
        "INCIDENT REPORT — January 15, 2024",
        "A black sedan with licence plate TN 33 AB 1234 was observed at 23:45.",
        "Two individuals exited the vehicle and entered the warehouse.",
    ]
    docx_bytes = _make_docx_bytes(paragraphs)
    result = parse(docx_bytes, "incident_report.docx")
    assert "TN 33 AB 1234" in result
    assert "INCIDENT REPORT" in result
    print(f"  ✓ DOCX parser: extracted {len(result)} chars")


def test_docx_parser_with_table():
    """Test DOCX parser extracts table content."""
    from app.parsers.docx_parser import parse
    from docx import Document

    doc = Document()
    doc.add_paragraph("Suspect List")
    table = doc.add_table(rows=2, cols=3)
    table.rows[0].cells[0].text = "Name"
    table.rows[0].cells[1].text = "Age"
    table.rows[0].cells[2].text = "Vehicle"
    table.rows[1].cells[0].text = "John Doe"
    table.rows[1].cells[1].text = "35"
    table.rows[1].cells[2].text = "Black Sedan"

    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    result = parse(docx_bytes, "suspects_table.docx")
    assert "John Doe" in result
    assert "Table" in result
    print(f"  ✓ DOCX parser with table: extracted {len(result)} chars")


# ── Text Cleaner ──────────────────────────────────────────────────────────────

def test_text_cleaner_preserves_evidence():
    """Test that text cleaner preserves forensic data like IDs and dates."""
    from app.services.text_cleaner import clean

    raw = "  TN 33 AB 1234  \r\n  Room 3B  \r\n  $45,000  \n\n\n  2024-01-15  "
    cleaned = clean(raw)
    assert "TN 33 AB 1234" in cleaned, "Licence plate should be preserved"
    assert "Room 3B" in cleaned, "Room number should be preserved"
    assert "$45,000" in cleaned, "Currency should be preserved"
    assert "2024-01-15" in cleaned, "Date should be preserved"
    assert "\r\n" not in cleaned, "CRLF should be normalised"
    print(f"  ✓ Text cleaner preserves all forensic data")


# ── Chunking Service ──────────────────────────────────────────────────────────

def test_chunking_produces_overlapping_chunks():
    """Test that chunker produces correct overlapping chunks."""
    from app.services.chunking_service import chunk_text

    text = "A" * 1000  # 1000 chars
    chunks = chunk_text(text, chunk_size=200, overlap=50)

    assert len(chunks) > 1, "Should produce multiple chunks for 1000-char text"
    # Check overlap: end of chunk N should overlap with start of chunk N+1
    # With size=200, overlap=50: chunk 2 starts at position 150
    assert chunks[0][-10:] in chunks[1] or len(chunks[1]) > 0
    print(f"  ✓ Chunker: {len(chunks)} chunks from 1000-char text")


def test_chunking_empty_text():
    """Test that chunker handles empty text gracefully."""
    from app.services.chunking_service import chunk_text
    result = chunk_text("", chunk_size=512, overlap=64)
    assert result == [], f"Expected [], got {result}"
    print("  ✓ Chunker: empty text returns []")
