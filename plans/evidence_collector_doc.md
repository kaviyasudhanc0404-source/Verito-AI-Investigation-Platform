# VERITO — Evidence Collector Agent
### Agent 01 | Technical Reference Document

---

## What It Does

The Evidence Collector is the **first agent** in the VERITO investigation pipeline. Its job is to take every file uploaded to an investigation, extract all readable text from it, clean the text, split it into searchable chunks, and store those chunks as vectors in Qdrant Cloud — so all other agents can search and reason over the evidence using semantic queries.

**One sentence:** It converts raw uploaded files (PDFs, emails, images, spreadsheets, etc.) into searchable vector embeddings stored in a cloud database.

---

## Folder Structure

```
backend/
└── app/
    ├── agents/
    │   └── evidence_collector.py       ← Agent entry point (orchestrator)
    │
    ├── parsers/                        ← One parser per file type
    │   ├── docling_parser.py           ← PDF, DOCX, PPTX, HTML, MD
    │   ├── image_parser.py             ← JPG, PNG, WEBP (OCR + AI description)
    │   ├── email_parser.py             ← .eml files
    │   ├── msg_parser.py               ← .msg Outlook files
    │   ├── csv_parser.py               ← .csv files
    │   ├── excel_parser.py             ← .xlsx / .xls files
    │   ├── xml_parser.py               ← .xml files
    │   ├── chat_parser.py              ← .json / .txt chat logs
    │   └── zip_extractor.py            ← .zip archives (recursive)
    │
    ├── services/
    │   ├── text_cleaner.py             ← Two-phase evidence-preserving cleaner
    │   ├── chunking_service.py         ← Structure-aware semantic chunker
    │   ├── embedding_service.py        ← BAAI/bge-small-en-v1.5 embeddings
    │   └── vector_store.py             ← Qdrant Cloud client + upsert logic
    │
    └── utils/
        └── file_detector.py            ← Maps file extension → type string
```

---

## Tech Stack

| Technology | What It Is Used For |
|---|---|
| **Python (FastAPI / asyncio)** | Agent runs as an async function. All heavy work (parsing, embedding) is offloaded to threads via `asyncio.to_thread()` so the server never blocks |
| **Docling** | Parses PDF, DOCX, PPTX, HTML, Markdown into structured Markdown. Preserves headings, tables, reading order, and runs built-in OCR on scanned PDFs |
| **EasyOCR** | Extracts visible printed text from image files (licence plates, documents, signage) |
| **Qwen2.5-VL (Vision LLM)** | Generates a natural-language scene description of image evidence to capture context not captured by OCR alone |
| **extract-msg** | Reads Microsoft Outlook `.msg` files (proprietary binary format) |
| **pandas + openpyxl + xlrd** | Reads all sheets from Excel files (`.xlsx` / `.xls`) into structured key-value rows |
| **lxml / ElementTree** | Walks XML trees, emits every element's tag path + text + attributes as searchable lines |
| **Python stdlib (csv, email, json, zipfile)** | CSV parsing, `.eml` email parsing, JSON chat log parsing, recursive ZIP extraction — no extra dependencies |
| **BAAI/bge-small-en-v1.5** | Sentence-transformer model that converts text chunks into 384-dimensional vectors. Used via `sentence-transformers` |
| **Qdrant Cloud** | Vector database where all embeddings are stored. Supports COSINE similarity search, payload filtering by `investigation_id`, and keyword indexes |
| **MongoDB (Motor async driver)** | Stores evidence metadata (filename, file path, cleaned content, evidence ID) and agent run logs with SSE activity lines |
| **Server-Sent Events (SSE)** | Streams real-time progress lines to the frontend as each file is processed (`emit("agent_activity", ...)`) |

---

## Working Flow

```
Investigation triggered
        │
        ▼
① Fetch all evidence docs for this investigation from MongoDB
        │
        ▼ (for each file)
② Read raw file bytes from disk  ← file_path stored in MongoDB at upload
        │
        ▼
③ Detect file type
   (file_detector maps extension → type string: "pdf", "image", "csv", etc.)
        │
        ▼
④ Parser Dispatch
   ┌──────────────────────────────────────────────────────────┐
   │  .pdf / .docx / .pptx / .html / .md  → docling_parser   │
   │  .jpg / .png / .webp / .bmp          → image_parser      │
   │  .eml                                → email_parser      │
   │  .msg                                → msg_parser        │
   │  .csv                                → csv_parser        │
   │  .xlsx / .xls                        → excel_parser      │
   │  .xml                                → xml_parser        │
   │  .json / .txt (chat)                 → chat_parser       │
   │  .zip                                → zip_extractor     │
   │  .txt (plain)                        → UTF-8 decode      │
   └──────────────────────────────────────────────────────────┘
        │  raw text string
        ▼
⑤ Phase 1 Cleaning (Basic)
   - Unicode NFC normalization
   - Remove control characters
   - Normalize line endings (CRLF → LF)
   - Collapse multiple spaces
   - Strip leading/trailing whitespace per line
   - Collapse 3+ blank lines to 2
        │
        ▼
⑥ Phase 2 Cleaning (Evidence-Preserving)
   - Normalize bullets (•, –, *, ► → -)    ← runs BEFORE line merging
   - Merge soft-wrapped continuation lines
   - Remove soft hyphens (U+00AD)
   - Verify URLs, Emails, Dates, IPs are untouched
        │
        ▼
⑦ Structure-Aware Semantic Chunking
   Priority:
     1. Split on section headings (# ## ### or ALL-CAPS or [Label])
     2. Split on paragraph blank lines
     3. Split on sentence boundaries (only when paragraph > 1200 chars)
   Result: semantically coherent chunks, 80–1200 characters each
        │  list of chunk strings
        ▼
⑧ Embedding
   BAAI/bge-small-en-v1.5 encodes each chunk → 384-dim float vector
        │
        ▼
⑨ Upsert to Qdrant Cloud
   Each vector stored with payload:
   { user_id, investigation_id, evidence_id, filename, chunk_index, text }
        │
        ▼
⑩ Update MongoDB
   evidence document → { content: cleaned_text, updated_at: now }
        │
        ▼
⑪ SSE stream → frontend shows real-time progress
   "E-001: Extracted 4,521 characters"
   "E-001: Split into 9 chunks"
   "E-001: Stored 9 vectors in Qdrant Cloud"
```

---

## Key Design Decisions

### Single Docling Entry Point
Instead of 5 separate parser files (pdf_parser, docx_parser, etc.), one function `parse_with_docling(file_bytes, filename, fmt)` handles all document formats. The `fmt` string selects the Docling `InputFormat`. This eliminates redundancy and makes adding new document types trivial.

### Evidence-Preserving Cleaner
Standard NLP pipelines remove stop words, stem words, and strip numbers. This cleaner deliberately does **none of that**. Forensic values like `TN 33 AB 1234`, `192.168.1.1`, `2024-01-15`, `$45,000` must survive cleaning exactly as-is because they are the evidence. A verification pass after Phase 2 raises a warning if any URL, email, date, or IP was accidentally lost.

### Structure-Aware Chunking
Rather than splitting every N characters (which cuts mid-sentence), the chunker reads the structural markers already present in Docling's Markdown output — headings, blank lines, [label] markers — and splits on those boundaries first. Sentence splitting is only used as a last resort within oversized paragraphs. This means each chunk maps to exactly one logical section of the document, giving the embedding model coherent, complete context.

### No Double Chunking
Chunks are computed once in the agent, then the same list is passed directly to `store_evidence_chunks(chunks=...)`. The vector store does not re-chunk internally. This ensures the chunks logged ("Split into 9 chunks") exactly match the chunks stored in Qdrant.

### Async + Thread Offloading
The agent is a Python async function. All CPU-heavy operations (Docling conversion, OCR, embedding generation) are wrapped in `asyncio.to_thread()`. This keeps the FastAPI event loop free to continue streaming SSE events to the frontend while processing is happening in background threads.

---

## What Makes This Agent Unique

| Feature | Why It Matters |
|---|---|
| **Unified multi-format ingestion** | 16 file types handled in one agent with a clean dispatch table — no code duplication |
| **Forensic-safe cleaning** | Preserves IPs, dates, emails, URLs, currency values, codes — nothing forensically important is stripped |
| **Docling for document structure** | Unlike basic PDF text extractors, Docling preserves heading hierarchy, reading order, and table structure, which feeds directly into better semantic chunks |
| **Dual image analysis** | Images go through both OCR (EasyOCR for text) and a vision LLM (Qwen2.5-VL for scene context) — two lenses on every image |
| **Recursive ZIP extraction** | A ZIP can contain any of the 16 supported file types; those files are individually parsed and concatenated — up to 2 nesting levels deep |
| **Real-time SSE progress** | Every step of every file is streamed live to the frontend — the user watches evidence being indexed in real time |
| **Payload-filtered Qdrant** | Every vector carries `investigation_id` so all 5 downstream agents can query only the vectors for their current case — no cross-contamination between investigations |

---

*Document covers: `evidence_collector.py`, `docling_parser.py`, `image_parser.py`, `email_parser.py`, `msg_parser.py`, `csv_parser.py`, `excel_parser.py`, `xml_parser.py`, `chat_parser.py`, `zip_extractor.py`, `text_cleaner.py`, `chunking_service.py`, `embedding_service.py`, `vector_store.py`*
