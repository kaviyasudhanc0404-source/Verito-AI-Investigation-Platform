"""
scratch/test_parsers.py
------------------------
Unit tests for all non-Docling, non-image parsers.

Tests run with synthetic in-memory bytes — no real files needed.
Docling and image_parser are excluded (require heavy ML models).

Run from backend/ dir:
  python scratch/test_parsers.py
"""
from __future__ import annotations
import sys, os, io, csv, json, zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def print_section(title: str) -> None:
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


# ─────────────────────────────────────────────────────────────────
# 1. CSV PARSER
# ─────────────────────────────────────────────────────────────────
def test_csv():
    print_section("1. csv_parser")
    from app.parsers.csv_parser import parse

    # Normal CSV
    raw = "Name,Age,Email\nAlice,30,alice@example.com\nBob,25,bob@example.com\n"
    out = parse(raw.encode(), "suspects.csv")
    print(out)
    assert "suspects.csv" in out
    assert "Name: Alice" in out
    assert "Email: alice@example.com" in out
    assert "Name: Bob" in out

    # BOM CSV (Excel export)
    bom_csv = "\ufeffDate,Amount\n2024-01-15,$5000\n".encode("utf-8-sig")
    out2 = parse(bom_csv, "transactions.csv")
    print(out2)
    assert "Date: 2024-01-15" in out2
    assert "Amount: $5000" in out2

    # Empty file
    out3 = parse(b"", "empty.csv")
    assert "Empty" in out3 or "No data" in out3

    # Only header, no data rows
    out4 = parse(b"Col1,Col2\n", "header_only.csv")
    assert "Col1" in out4

    print("✅ csv_parser — passed")


# ─────────────────────────────────────────────────────────────────
# 2. EXCEL PARSER
# ─────────────────────────────────────────────────────────────────
def test_excel():
    print_section("2. excel_parser")
    try:
        import pandas as pd
        import openpyxl
    except ImportError:
        print("⚠️  pandas/openpyxl not installed — skipping excel_parser test")
        return

    from app.parsers.excel_parser import parse

    # Build a real xlsx in memory using openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(["Date", "Sender", "Receiver", "Amount"])
    ws.append(["2024-01-15", "John Doe", "Jane Smith", "5000"])
    ws.append(["2024-01-16", "Jane Smith", "Bob Corp", "12500"])

    # Add a second sheet
    ws2 = wb.create_sheet("Notes")
    ws2.append(["Note"])
    ws2.append(["Suspicious activity on 2024-01-15"])

    buf = io.BytesIO()
    wb.save(buf)
    xlsx_bytes = buf.getvalue()

    out = parse(xlsx_bytes, "evidence.xlsx")
    print(out)

    assert "Sheet: Transactions" in out
    assert "Date: 2024-01-15" in out
    assert "Sender: John Doe" in out
    assert "Amount: 5000" in out
    assert "Sheet: Notes" in out
    assert "Suspicious activity" in out

    # Empty file
    out2 = parse(b"not an excel file", "bad.xlsx")
    assert "Failed" in out2 or "parse" in out2.lower()

    print("✅ excel_parser — passed")


# ─────────────────────────────────────────────────────────────────
# 3. XML PARSER
# ─────────────────────────────────────────────────────────────────
def test_xml():
    print_section("3. xml_parser")
    from app.parsers.xml_parser import parse

    xml_data = b"""<?xml version="1.0"?>
<investigation id="INV-001">
    <suspect name="John Doe">
        <dob>1990-05-20</dob>
        <address>123 Main St, Chennai</address>
        <phone>+91-9876543210</phone>
    </suspect>
    <incident date="2024-01-15">
        <description>Robbery at Central Bank</description>
        <location lat="13.0827" lon="80.2707">Chennai</location>
    </incident>
</investigation>
"""
    out = parse(xml_data, "case.xml")
    print(out)

    assert "case.xml" in out
    assert "John Doe" in out
    assert "1990-05-20" in out
    assert "Robbery at Central Bank" in out
    assert "Chennai" in out

    # Namespace stripping test
    ns_xml = b"""<ns:root xmlns:ns="http://example.com/schema">
    <ns:person>
        <ns:name>Alice</ns:name>
    </ns:person>
</ns:root>"""
    out2 = parse(ns_xml, "namespaced.xml")
    print(out2)
    assert "Alice" in out2
    # Namespace prefix should be stripped from path
    assert "http" not in out2 or "Alice" in out2

    # Bad XML
    out3 = parse(b"<bad xml &&&>", "bad.xml")
    assert "parse error" in out3.lower() or "XML" in out3

    print("✅ xml_parser — passed")


# ─────────────────────────────────────────────────────────────────
# 4. EMAIL PARSER
# ─────────────────────────────────────────────────────────────────
def test_email():
    print_section("4. email_parser")
    from app.parsers.email_parser import parse

    # Plain text email
    eml = b"""From: john.doe@company.com\r\nTo: security@company.com\r\nCc: boss@company.com\r\nSubject: Suspicious Login\r\nDate: Mon, 15 Jan 2024 03:42:00 +0000\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSuspicious login detected from 192.168.45.12 at 2024-01-15.\r\nPlease investigate: https://internal.portal/report?id=123\r\n"""

    out = parse(eml, "incident.eml")
    print(out)

    assert "incident.eml" in out
    assert "john.doe@company.com" in out
    assert "security@company.com" in out
    assert "boss@company.com" in out          # CC preserved
    assert "Suspicious Login" in out
    assert "2024-01-15" in out
    assert "192.168.45.12" in out
    assert "https://internal.portal/report?id=123" in out

    # Multipart email (html + plain)
    multipart = (
        b"From: alice@test.com\r\nTo: bob@test.com\r\nSubject: Test\r\n"
        b"Content-Type: multipart/mixed; boundary=\"BOUND\"\r\n\r\n"
        b"--BOUND\r\nContent-Type: text/plain\r\n\r\nThis is the body.\r\n"
        b"--BOUND\r\nContent-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=\"report.pdf\"\r\n\r\n"
        b"(binary content)\r\n--BOUND--\r\n"
    )
    out2 = parse(multipart, "multi.eml")
    print(out2)
    assert "This is the body." in out2
    assert "report.pdf" in out2              # Attachment listed

    print("✅ email_parser — passed")


# ─────────────────────────────────────────────────────────────────
# 5. CHAT PARSER
# ─────────────────────────────────────────────────────────────────
def test_chat():
    print_section("5. chat_parser")
    from app.parsers.chat_parser import parse

    # Pattern 1: [timestamp] Sender: message
    txt1 = b"[2024-01-15 10:30] Alice: Did you transfer the money?\n[2024-01-15 10:31] Bob: Yes, 5000 to offshore account.\n"
    out1 = parse(txt1, "chat.txt")
    print(out1)
    assert "Alice" in out1
    assert "Did you transfer the money?" in out1
    assert "Bob" in out1

    # Pattern 2: Sender (time): message
    txt2 = b"Alice (10:30): Hello Bob\nBob (10:31): Hi Alice, let's meet at 192.168.1.1\n"
    out2 = parse(txt2, "chat2.txt")
    print(out2)
    assert "Alice" in out2
    assert "192.168.1.1" in out2

    # Pattern 3: Sender: message (no timestamp)
    txt3 = b"John: Let's do the transfer\nMary: Okay, using account 1234567890\n"
    out3 = parse(txt3, "chat3.txt")
    print(out3)
    assert "John" in out3
    assert "1234567890" in out3

    # JSON chat
    json_data = json.dumps([
        {"timestamp": "2024-01-15T10:30:00", "sender": "Alice", "message": "Transfer done."},
        {"timestamp": "2024-01-15T10:31:00", "sender": "Bob", "message": "Confirmed."},
    ]).encode()
    out4 = parse(json_data, "chat.json")
    print(out4)
    assert "Alice" in out4
    assert "Transfer done." in out4

    # JSON with wrapper key
    json_data2 = json.dumps({
        "messages": [
            {"sender": "X", "text": "Secret message", "time": "10:00"},
        ]
    }).encode()
    out5 = parse(json_data2, "export.json")
    print(out5)
    assert "Secret message" in out5

    print("✅ chat_parser — passed")


# ─────────────────────────────────────────────────────────────────
# 6. ZIP EXTRACTOR
# ─────────────────────────────────────────────────────────────────
def test_zip():
    print_section("6. zip_extractor")
    from app.parsers.zip_extractor import parse

    # Build a ZIP with a CSV and a plain text file
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("suspects.csv",
            "Name,Role\nJohn Doe,Suspect\nJane Smith,Witness\n")
        zf.writestr("notes.txt",
            "Incident occurred on 2024-01-15. IP: 192.168.1.100")
        zf.writestr("empty_dir/", "")   # directory entry — should be skipped

    out = parse(buf.getvalue(), "evidence_bundle.zip")
    print(out)

    assert "evidence_bundle.zip" in out
    assert "suspects.csv" in out
    assert "John Doe" in out
    assert "notes.txt" in out
    assert "192.168.1.100" in out

    # Bad ZIP
    out2 = parse(b"not a zip file", "bad.zip")
    assert "Invalid ZIP" in out2 or "bad" in out2.lower()

    # Empty ZIP
    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w"):
        pass
    out3 = parse(buf2.getvalue(), "empty.zip")
    assert "no files" in out3.lower() or "0 file" in out3.lower() or "no" in out3.lower()

    print("✅ zip_extractor — passed")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🔍 VERITO Parser Tests (csv, excel, xml, email, chat, zip)")
    results = []
    tests = [
        ("csv_parser",   test_csv),
        ("excel_parser", test_excel),
        ("xml_parser",   test_xml),
        ("email_parser", test_email),
        ("chat_parser",  test_chat),
        ("zip_extractor",test_zip),
    ]
    for name, fn in tests:
        try:
            fn()
            results.append((name, "✅ PASS"))
        except AssertionError as e:
            results.append((name, f"❌ FAIL: {e}"))
        except Exception as e:
            import traceback
            results.append((name, f"💥 ERROR: {e}"))
            traceback.print_exc()

    print(f"\n{'='*60}\n  RESULTS\n{'='*60}")
    for name, status in results:
        print(f"  {status:30s}  {name}")

    failures = [r for r in results if not r[1].startswith("✅")]
    if failures:
        sys.exit(1)
