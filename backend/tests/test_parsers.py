"""
Ingestion: parsers, text cleaner, chunker and file handling.
"""

import pytest

from app.agents import evidence_collector
from app.parsers import PARSE_WARNING, format_record, parse_bytes
from app.services.chunking_service import MAX_CHUNK_CHARS, chunk_text
from app.services.text_cleaner import clean
from app.utils.file_detector import detect_file_type, detect_kind, is_supported
from app.utils.file_utils import safe_display_name
from app.utils.id_generator import format_evidence_id
from tests.conftest import FIXTURES


def _parse(name: str) -> str:
    return parse_bytes((FIXTURES / name).read_bytes(), name)


# ── Parsers ───────────────────────────────────────────────────────────────────

def test_csv_rows_render_as_records():
    text = _parse("bank_transactions.csv")
    assert "Total rows: 7" in text
    assert "[Row 1] Transaction ID: TXN-2026-0318 | Transaction Date: 15-Mar-2026" in text
    assert "Amount: ₹2,34,230" in text


def test_chat_json_keeps_every_participant_detail():
    text = _parse("chat_records.json")
    first = next(line for line in text.splitlines() if line.startswith("[Message 1]"))
    assert "Time: 2026-03-06T10:14:22+05:30" in first
    assert "From: Arvind Kumaraswamy (MIL-EMP-0587)" in first
    assert "To: Sanjay Bhaskaran (MIL-EMP-0761)" in first
    assert "Message ID: CHAT-2026-0306-01" in first and "References: CC-4410" in first


def test_event_json_renders_one_record_per_event():
    text = _parse("system_audit.json")
    records = [line for line in text.splitlines() if line.startswith("[Record ")]
    assert len(records) == 13
    assert "Action: CREATE_PO" in records[1] and "Record ID: PO-2026-0342" in records[1]


def test_multi_message_email_is_split_and_decoded():
    text = _parse("emails.eml")
    headers = [line for line in text.splitlines() if line.startswith("[Email ") and line[7].isdigit()]
    assert len(headers) == 3
    assert "Subject: PO-2026-0342 – Site B requirement" in headers[1]       # =?utf-8?b?…?= decoded
    assert "Date: 2026-03-13T17:12:04+05:30" in headers[2]
    assert "From: accounts@kovai-build.example" in headers[2]


def test_format_record_skips_empty_values_and_escapes_separators():
    line = format_record("Row", 2, [("A", "x|y"), ("B", ""), ("C", "2026-03-05 00:00:00")])
    assert line == "[Row 2] A: x/y | C: 2026-03-05"


# ── Cleaner and chunker ───────────────────────────────────────────────────────

def test_cleaner_keeps_structure_and_decodes_entities():
    raw = "| a | b |\n|------|------|\n| c | d |\n[Email 1] Subject: Site B\nHi Sanjay,\nA &amp; B \\_\\_\\_\\_ ok\nwrapped line\ncontinues here."
    cleaned = clean(raw)
    assert "| a | b |\n|---|---|\n| c | d |" in cleaned            # table rows never merged
    assert "[Email 1] Subject: Site B\nHi Sanjay," in cleaned      # record line not merged with body
    assert "A & B ok" in cleaned
    assert "wrapped line continues here." in cleaned


def test_chunks_respect_limit_and_keep_records_whole():
    rows = "\n".join(f"[Row {i}] Event ID: AUD-{i:04d} | Action: CREATE_PO | Note: {'x' * 80}" for i in range(1, 60))
    chunks = chunk_text(f"[Sheet: Audit]\n{rows}")
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks)
    assert all(line.startswith(("[Sheet", "[Row")) for chunk in chunks for line in chunk.splitlines())


def test_split_table_repeats_its_header():
    header = "| PO No. | Vendor |\n|---|---|"
    rows = "\n".join(f"| PO-2026-{i:04d} | {'Kovai Building Solutions ' * 3}|" for i in range(40))
    chunks = chunk_text(f"## Purchase Orders\n{header}\n{rows}")
    assert len(chunks) > 1
    assert all("| PO No. | Vendor |" in chunk for chunk in chunks)


def test_empty_text_has_no_chunks():
    assert chunk_text("") == []


def test_short_files_are_never_dropped():
    assert chunk_text("[Row 1] User: bob | Action: LOGIN") == ["[Row 1] User: bob | Action: LOGIN"]
    assert chunk_text("Approved.") == ["Approved."]


# ── Malformed input is reported, not lost ─────────────────────────────────────

def test_invalid_json_is_kept_as_text_with_a_warning():
    text = clean(parse_bytes(b'{"user": "j.doe", "action": [1, 2', "broken.json"))
    assert '"user": "j.doe"' in text
    assert [m["text"] for m in PARSE_WARNING.finditer(text)] == [
        "not valid JSON (line 1, column 34); read as plain text"]


def test_empty_and_invalid_files_yield_no_text_or_raise():
    assert parse_bytes(b"", "empty.csv") == "" and parse_bytes(b"zzz", "notes.unknown") == ""
    with pytest.raises(ValueError, match="not a valid ZIP"):
        parse_bytes(b"not a zip", "bundle.zip")


def test_collector_profile_reports_extraction_problems():
    text = clean(parse_bytes(b"A,B\n", "header_only.csv"))
    profile = evidence_collector._profile("header_only.csv", text)
    assert profile["records"] == 0 and any("No structured records" in w for w in profile["warnings"])
    broken = evidence_collector._profile("broken.json", parse_bytes(b"{oops", "broken.json"))
    assert any(w.startswith("Not valid JSON") for w in broken["warnings"])


def test_page_markers_give_records_their_page():
    from app.parsers.docling_parser import _PAGE_BREAK, _with_page_markers
    from app.services.records import extract_records

    text = _with_page_markers(f"Intro{_PAGE_BREAK}| ID | Amount |\n|---|---|\n| INV-2026-0001 | $10 |")
    assert text.startswith("[Page 1]") and "[Page 2]" in text
    record = extract_records("E-001", "scan.pdf", text)[0]
    assert record.page == 2 and record.location == "page 2, table row 1"


# ── Files and IDs ─────────────────────────────────────────────────────────────

def test_file_types_and_kinds():
    assert detect_file_type("report.PDF") == "pdf" and detect_kind("report.pdf") == "doc"
    assert detect_file_type("chat.json") == "json" and detect_kind("chat.json") == "text"
    assert detect_kind("mail.eml") == "chat" and detect_kind("call.mp3") == "audio"
    assert is_supported("photo.heic") and not is_supported("tool.exe")


def test_client_filenames_cannot_escape_the_upload_folder():
    assert safe_display_name("../../app/main.py") == "main.py"
    assert safe_display_name("..\\..\\secret.pdf") == "secret.pdf"
    assert safe_display_name("") == "unnamed_file"


def test_format_evidence_id():
    assert format_evidence_id(7) == "E-007"


def test_images_embedded_in_pdfs_are_extracted():
    import io
    import os

    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    from app.parsers.document_image_extractor import extract_images

    photo = Image.frombytes("RGB", (320, 240), os.urandom(320 * 240 * 3))      # incompressible, not an icon
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawImage(ImageReader(photo), 72, 400, 320, 240)
    page.showPage()
    page.save()
    images = extract_images(buffer.getvalue(), "pdf")
    assert len(images) == 1 and images[0].label == "Page 1 - Image 1" and images[0].content_type == "image/png"
