# VERITO Backend

AI investigation platform — FastAPI backend. Upload case evidence (documents, spreadsheets,
emails, chat exports, logs, images, audio); seven agents turn it into a cited, validated,
plain-language case report. Nothing is specific to one kind of case: the same pipeline handles
procurement, finance, HR / compliance, access-log and other investigations.

## Tech stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Uvicorn |
| Database | MongoDB Atlas (Motor) |
| Vector store | Qdrant Cloud |
| Embeddings | `BAAI/bge-small-en-v1.5` (sentence-transformers, 384-d) |
| LLM / transcription | Groq (`GROQ_MODEL`, default `openai/gpt-oss-20b`) / Groq Whisper |
| Document parsing | Docling (PDF, DOCX, PPTX, HTML, Markdown); pandas/openpyxl; stdlib csv/json/email |
| Images | EasyOCR + Gemini vision captions |
| Auth | JWT + Argon2, Firebase (Google sign-in) |
| Report export | ReportLab (PDF), python-docx (Word) |

## Project structure

```
backend/
├── app/
│   ├── main.py            FastAPI app and routers
│   ├── api/               HTTP routes (auth, investigations + SSE stream, evidence, reports, chat, agents)
│   ├── agents/            the seven pipeline agents + base.py (shared run/status/log plumbing)
│   ├── workflows/         pipeline.py — runs the agents, one live run per investigation
│   ├── parsers/           one parser per file type; parse_evidence() is the single dispatch
│   ├── services/          values, records, identities, case_model, reconciliation/ (core + domain
│   │                      adapters), validation, document_facts, llm, vector store, …
│   ├── schemas/           Pydantic request/response models
│   ├── database/          MongoDB connection and collections
│   ├── core/              settings, security, exceptions
│   ├── dependencies/      auth + investigation-ownership dependencies
│   └── utils/             file types, safe file storage, ids, time
├── scripts/run_pipeline.py   run the pipeline for one investigation from the terminal
├── tests/                 pytest suite; fixtures/: meridian (procurement golden case), cyber,
│                          conflicts, hr — synthetic multi-domain cases
└── requirements.txt
```

## Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows  (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
```

Create `backend/.env`:

```env
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MONGODB_DATABASE=verito
JWT_SECRET=a-long-random-secret
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-20b
QDRANT_URL=https://<cluster-id>.<region>.cloud.qdrant.io
QDRANT_API_KEY=...
QDRANT_COLLECTION=verito_evidence
GEMINI_API_KEY=...             # optional: image captions
FIREBASE_PROJECT_ID=...
FIREBASE_CLIENT_EMAIL=...
FIREBASE_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...
FRONTEND_URL=http://localhost:3000

# Optional tuning (defaults shown)
GROQ_TIMEOUT_SECONDS=60
GROQ_CONNECT_TIMEOUT_SECONDS=10
GROQ_MAX_ATTEMPTS=5               # per request: rate limits, timeouts, 5xx
QUALITY_GATE_POLICY=partial       # warn | partial | block
QUALITY_MAX_FALLBACK_RATIO=0.5    # share of document sections analysed without AI
QUALITY_MAX_FAILED_FILE_RATIO=0.25
QUALITY_MAX_REJECTED_FINDINGS=0
```

The Qdrant collection and its payload indexes are created automatically on first use.

Run the API (docs at http://localhost:8000/docs):

```bash
uvicorn app.main:app --reload --port 8000
```

## How an investigation works

```
Evidence Collector  parse → clean → chunk (≤1,200 chars, records/tables kept whole, nothing dropped) →
                    embed → Qdrant; an ingestion report per file (status, records, chunks, pages, OCR, warnings)
Evidence Analyzer   structured rows → typed records with provenance; narrative text → LLM (people, verbatim
                    quotes, findings; schema-validated); builds the Case model (records, identities, copies)
Timeline Builder    one event per dated record or sentence — date type, original timestamp, timezone,
                    approximate flag — merged across files and ordered in UTC
Evidence Linker     how files relate (shared identifiers / parties, conflicting records, duplicate copies)
                    + reconciliation: generic core checks and the domain adapters that apply
Finding Validator   re-checks every finding against its sources, grades claim type and confidence,
                    applies the quality gate
Subject Analyzer    what each person or account did, with stated involvement criteria and identity notes
Report Generator    summary, what the records establish / suggest / leave uncertain / what to verify next,
                    findings, reconciliation, timeline, people, connections, evidence inventory, limitations,
                    methodology, diagnostics
```

**Records understand themselves.** Every field gets a role (actor, action, target, outcome, IP,
amount, counterparty, …) from its name, so an unseen CSV or JSON log still yields entities, timeline
events and links. Timestamps keep their stated UTC offset; ones without an offset are assumed to use
the case's dominant offset and the report says so.

**Reconciliation** (`services/reconciliation/`) — deterministic, so every red flag can be checked:

| Module | Runs when | Checks |
|---|---|---|
| `core.py` | always | same ID with conflicting values across sources, amounts that don't match the referenced document, duplicates, out-of-order dates, references to records missing from the evidence |
| `procurement.py` | orders / invoices / payments exist | orders just under an approval limit, self-approval, vendor set up just before orders, unusually fast payment, payment before invoice, payment-date mismatches |
| `communications.py` | calls / chats / emails exist | contact with a party during its approvals, repeated direct contact |
| `access.py` | activity logs with accounts exist | failed then successful sign-ins, use from a new address, activity after a security alert, night-time activity |

Adapters run first; core checks skip anything an adapter already explains. Each observation carries
its records (with excerpts), any calculation, context that may explain it, and how to verify it.

**Validation** (`services/validation.py`). Before reporting, every finding must cite files in the
case, quote verbatim, mention only identifiers / amounts / dates found in its sources, recompute its
calculation and use neutral language; failures are withheld with reasons. Accepted findings are
labelled *Established by the records*, *Documented observation*, *Pattern requiring review* or
*Reported statement*, with a confidence level and its basis (independent sources — copies of the same
data count once — and identity certainty).

**Quality gate.** If too many files failed, too much text was analysed without AI, or findings
were withheld, `QUALITY_GATE_POLICY` decides: `warn` (note it), `partial` (report marked partial
with reasons) or `block` (no report).

**The LLM** (Groq) extracts names and quotes from narrative text, summarises dated sentences and
words the title, scope and questions — never the findings. Calls have explicit timeouts, retries for
transient errors, immediate clear errors for a bad key or model, and per-call logging (purpose,
model, latency, tokens, outcome — never keys or evidence text). If it is unavailable the report is
still complete, written from the rules, and the affected agents finish as *completed using fallback*.

Agent statuses: `idle` (pending), `running`, `done`, `completed_with_warnings`,
`completed_with_fallback`, `failed`, `blocked`. Per-agent diagnostics (duration, counts, LLM calls,
failures, fallbacks) are saved with the report.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/signup` · `/api/auth/login` · `/api/auth/google` | Sign up / sign in |
| GET · PATCH | `/api/auth/me` | Current user / update name, email, password |
| GET · POST | `/api/investigations` | List / create investigations |
| GET · PATCH · DELETE | `/api/investigations/{id}` | Read / rename or pin / delete (incl. vectors) |
| GET | `/api/investigations/{id}/stream` | SSE stream of the pipeline (starts it, or attaches to the running one) |
| POST | `/api/evidence/upload/{id}` | Upload evidence files |
| GET · DELETE | `/api/evidence/{id}` | List / delete an investigation's evidence |
| GET | `/api/agents/{id}` | Agent run states |
| GET | `/api/reports/{id}` | Final report |
| GET | `/api/reports/{id}/export/{pdf\|docx\|json}` | Download the report |
| GET · POST | `/api/chat/{id}` | Follow-up Q&A history / ask a question |

SSE events: `agent_started`, `agent_activity` (`line: {kind, text}`), `agent_completed`,
`agent_completed_with_warnings`, `agent_completed_with_fallback`, `agent_failed` (`error`), `agent_blocked` (`reason`),
`investigation_completed`, `stream_end`. A `: keep-alive` comment is sent every 15 s.

## Tests and tools

```bash
pytest                                       # full suite; external APIs mocked (test_qdrant.py needs Qdrant credentials)
RUN_GROQ_INTEGRATION=1 pytest -m integration # one live Groq call using .env credentials
python -m scripts.run_pipeline <inv_id>      # re-run one investigation and print its report
```

## Notes

- Uploads are stored as `uploads/<user_id>/<investigation_id>/<random>.<ext>`; the original
  filename is kept only as metadata.
- Every query and vector is filtered by `investigation_id` and `user_id`.
- Evidence IDs (`E-001`, …) come from an atomic per-investigation counter and are never reused.
