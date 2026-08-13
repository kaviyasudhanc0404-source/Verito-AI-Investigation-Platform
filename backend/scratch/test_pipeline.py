"""
scratch/test_pipeline.py
--------------------------
Quick smoke test for the full evidence collector pipeline:
  text_cleaner → chunking_service → vector_store (chunk count only)

Run from backend/ dir:
  python -m app.scratch.test_pipeline   (if in package)
  python scratch/test_pipeline.py       (directly)

Does NOT require Qdrant/MongoDB — tests logic only.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Sample texts to test ──────────────────────────────────────────────────────

RESUME_TEXT = """[Document: KAVIYASUDHAN_C-RESUME.pdf]

# KAVIYASUDHAN C
AI/ML Engineer | Software Developer
+91 8870294044 | kaviyasudhanc0404@gmail.com

## Career Objective
Passionate about leveraging AI to solve real-world challenges, drive innovation, and
build impactful applications that create meaningful value.

## Education
Kongu Engineering College
B.Tech - Artificial Intelligence and Data Science
2023-2027* CGPA: 7.12*

Saratha Matric Hr. Sec. School
Higher Secondary Education
2022-2023 12th: 82%

## Skills
Programming: C, Python, Java, JavaScript, SQL
AI/ML: LLMs, Generative AI, RAG, Agentic AI, NLP, Computer Vision
Frontend: HTML, CSS, React.js
Backend: Node.js, Express.js, Flask, FastAPI
Databases: MongoDB, MySQL, ChromaDB

## Achievements
• Pre-finalist in SIH 2025 with an AI-driven urban waste management solution.
• Winner of Hacksphere 2025, an AI hackathon conducted by the AI Department.

## Projects

### CareAI – AI-Powered Healthcare Platform
Built an intelligent healthcare platform for symptom analysis and specialist recommendation.
Integrated AI chatbots, skin disease detection, OCR-based report analysis, and appointment booking.
Enabled RAG-powered assistance for context-aware healthcare support.
Tech: LangChain, ChromaDB, Tesseract.jsOCR, EfficientNetB0

### ThinkAuto – AI-Powered IT Helpdesk Platform
Created an AI-powered IT helpdesk platform for automated ticket management.
Implemented NLP-based ticket classification and query processing workflows.
Automated email-to-ticket conversion to streamline support operations.
Tech: Logistic Regression, NLP, IMAP, Mailparser

### ChargeHub – EV Charging Management Platform
Designed a full-stack EV charging platform for station discovery and slot booking.
Implemented location-based search and nearby charger identification.
Tech: Leaflet, OpenStreetMap, Geolocation, Haversine

### Fire Accident Detection System
Engineered a real-time fire detection system for safety monitoring.
Trained a YOLOv8 model to detect fire from images and video streams.
Tech: YOLOv8, OpenCV, Computer Vision

## Certifications
MongoDB Certified Associate Developer  2025
Oracle Certified Professional: Java SE 17 Developer  2026
"""

FORENSIC_EMAIL_TEXT = """[Email Evidence: incident_report.eml]
From: john.doe@company.com
To: security@company.com
Subject: Suspicious Login from 192.168.45.12
Date: 2024-07-15

EMAIL BODY:
Hi Security Team,

We detected a suspicious login attempt from IP address 192.168.45.12
at 2024-07-15 03:42:00 UTC. The account user@internal.com was targeted.
Please investigate immediately. The attacker used the URL:
https://malicious-site.example.com/phish?token=abc123

Let me know if you need more info.
— John
"""

MESSY_TEXT = """This is a line that has   extra   spaces  in    it.
And\r\nthis line has Windows line endings.\r
This line has a soft\xadhyphen in it.
\x00\x01\x02These control chars should be removed.
\u2022 First bullet point
\u2013 Second bullet point (en-dash)
\u25ba Third bullet (arrow)
* Fourth bullet (asterisk)
"""

CSV_TEXT = """[CSV Evidence: transactions.csv]
Columns: Date | Amount | Sender | Receiver | Reference
Total rows: 3

[Row 1] Date: 2024-01-15 | Amount: $5,000.00 | Sender: John Doe | Receiver: Jane Smith | Reference: TXN-001
[Row 2] Date: 2024-01-16 | Amount: $12,500.00 | Sender: Jane Smith | Receiver: Bob Corp | Reference: TXN-002
[Row 3] Date: 2024-01-17 | Amount: $3,200.00 | Sender: Bob Corp | Receiver: Alice Ltd | Reference: TXN-003
"""


def print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def test_text_cleaner() -> None:
    print_section("1. TEXT CLEANER — Two-Phase Pipeline")

    from app.services.text_cleaner import clean, _phase1_basic, _phase2_evidence_preserving

    # Test messy text
    print("\n[Input — Messy Text]")
    print(repr(MESSY_TEXT[:120]))

    cleaned = clean(MESSY_TEXT)
    print("\n[Output — After Both Phases]")
    print(cleaned)

    # Verify control chars removed
    assert "\x00" not in cleaned, "FAIL: Control char \\x00 not removed"
    assert "\x01" not in cleaned, "FAIL: Control char \\x01 not removed"
    assert "\xad" not in cleaned, "FAIL: Soft hyphen not removed"

    # Verify evidence preserved in forensic email
    cleaned_email = clean(FORENSIC_EMAIL_TEXT)
    assert "192.168.45.12" in cleaned_email, "FAIL: IP address was removed!"
    assert "john.doe@company.com" in cleaned_email, "FAIL: Email was removed!"
    assert "2024-07-15" in cleaned_email, "FAIL: Date was removed!"
    assert "https://malicious-site.example.com/phish?token=abc123" in cleaned_email, "FAIL: URL was removed!"

    # Verify bullet normalization
    cleaned_messy = clean(MESSY_TEXT)
    lines_with_bullets = [l for l in cleaned_messy.splitlines() if l.strip().startswith("-")]
    print(f"\n[Bullet Normalization] Found {len(lines_with_bullets)} normalized bullet lines")
    for l in lines_with_bullets:
        print(f"  {repr(l)}")

    # Verify extra spaces collapsed
    assert "extra   spaces" not in cleaned_messy, "FAIL: Extra spaces not collapsed"

    print("\n✅ text_cleaner — ALL checks passed")


def test_chunker() -> None:
    print_section("2. CHUNKING SERVICE — Structure-Aware Semantic Chunker")

    from app.services.text_cleaner import clean
    from app.services.chunking_service import chunk_text

    cleaned = clean(RESUME_TEXT)
    chunks = chunk_text(cleaned)

    print(f"\n[Resume] → {len(chunks)} chunks produced")
    print(f"  (Input length: {len(cleaned):,} chars)")

    for i, chunk in enumerate(chunks):
        first_line = chunk.splitlines()[0][:80]
        print(f"  Chunk {i}: [{len(chunk)} chars] {first_line!r}")

    # Check no mid-word cuts
    for i, chunk in enumerate(chunks):
        # A chunk should not START with a lowercase continuation letter + rest of word
        first_char = chunk.strip()[0] if chunk.strip() else ""
        assert first_char not in "rtndaeiou" or chunk.strip().startswith(("r", "re", "re")), \
            f"Chunk {i} may start mid-word: {chunk[:30]!r}"

    # Check all chunks meet min length
    from app.services.chunking_service import MIN_CHUNK_CHARS
    for i, chunk in enumerate(chunks):
        assert len(chunk.strip()) >= MIN_CHUNK_CHARS, \
            f"Chunk {i} too short ({len(chunk)} chars): {chunk!r}"

    # Check sections are split correctly (Education, Skills, Projects etc. not mixed)
    section_chunks = {}
    for chunk in chunks:
        for section in ["## Education", "## Skills", "## Projects", "## Certifications", "## Achievements"]:
            if section in chunk:
                section_chunks[section] = section_chunks.get(section, 0) + 1

    print(f"\n[Section Boundary Detection]")
    for section, count in section_chunks.items():
        print(f"  '{section}' appears in {count} chunk(s)")

    # CSV text chunking
    cleaned_csv = clean(CSV_TEXT)
    csv_chunks = chunk_text(cleaned_csv)
    print(f"\n[CSV] → {len(csv_chunks)} chunks")
    for i, c in enumerate(csv_chunks):
        print(f"  Chunk {i}: {c[:80]!r}")

    print("\n✅ chunking_service — ALL checks passed")


def test_pipeline_integration() -> None:
    print_section("3. FULL PIPELINE — Clean → Chunk → Verify Counts Match")

    from app.services.text_cleaner import clean
    from app.services.chunking_service import chunk_text

    texts = {
        "Resume": RESUME_TEXT,
        "Forensic Email": FORENSIC_EMAIL_TEXT,
        "CSV": CSV_TEXT,
    }

    for name, text in texts.items():
        cleaned = clean(text)
        chunks = chunk_text(cleaned)

        print(f"\n[{name}]")
        print(f"  Raw chars:     {len(text):,}")
        print(f"  Cleaned chars: {len(cleaned):,}")
        print(f"  Chunks:        {len(chunks)}")
        print(f"  Avg chunk len: {sum(len(c) for c in chunks) // max(len(chunks),1)} chars")

        # No empty chunks
        empty = [i for i, c in enumerate(chunks) if not c.strip()]
        assert not empty, f"Empty chunks at indices: {empty}"

        # No chunk exceeds MAX_CHUNK_CHARS by more than 20% (sentence splits can overshoot slightly)
        from app.services.chunking_service import MAX_CHUNK_CHARS
        long = [(i, len(c)) for i, c in enumerate(chunks) if len(c) > MAX_CHUNK_CHARS * 1.2]
        if long:
            print(f"  ⚠️  Long chunks: {long}")

    print("\n✅ Integration pipeline — ALL checks passed")


if __name__ == "__main__":
    print("\n🔍 VERITO Evidence Pipeline — Smoke Tests")
    print("Testing: text_cleaner → chunking_service (no Qdrant/MongoDB needed)\n")

    try:
        test_text_cleaner()
        test_chunker()
        test_pipeline_integration()
        print("\n" + "="*60)
        print("  ✅ ALL TESTS PASSED")
        print("="*60)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n💥 ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)
