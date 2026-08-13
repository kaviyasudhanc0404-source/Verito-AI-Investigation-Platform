# VERITO Backend

## AI Investigation Platform — Python/FastAPI Backend

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | FastAPI + Uvicorn |
| Database | MongoDB Atlas (Motor async driver) |
| Vector Store | Chroma Cloud |
| LLM | Groq API (llama3-70b-8192) |
| Transcription | Groq Whisper |
| AI Workflow | LangGraph |
| Auth | JWT + Argon2 + Firebase Admin SDK |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) |
| Document Parsing | PyMuPDF, python-docx |
| OCR | pytesseract + Pillow |

---

## Project Structure

```
backend/
├── app/
│   ├── main.py               # FastAPI app entry point
│   ├── api/                  # Route handlers
│   ├── agents/               # 6 AI investigation agents
│   ├── workflows/            # LangGraph pipeline
│   ├── services/             # Business logic & external services
│   ├── database/             # MongoDB connection & collections
│   ├── models/               # MongoDB document models
│   ├── schemas/              # Pydantic request/response schemas
│   ├── core/                 # Config, security, exceptions
│   ├── dependencies/         # FastAPI dependencies (auth)
│   └── utils/                # Helper utilities
├── uploads/                  # Uploaded evidence files
├── tests/                    # Test suite
├── .env                      # Environment variables (fill in your keys)
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Create a virtual environment

```bash
cd backend
python -m venv venv
venv\Scripts\activate       # Windows
# or
source venv/bin/activate    # macOS/Linux
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Fill in `backend/.env` with your credentials:

```env
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MONGODB_DATABASE=verito
JWT_SECRET=your-super-secret-key-here
GROQ_API_KEY=gsk_...
CHROMA_API_KEY=...
CHROMA_TENANT=your-tenant
CHROMA_DATABASE=your-database
FIREBASE_PROJECT_ID=...
FIREBASE_CLIENT_EMAIL=...
FIREBASE_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...
FRONTEND_URL=http://localhost:3000
```

### 4. Run the server

```bash
uvicorn app.main:app --reload --port 8000
```

API documentation: http://localhost:8000/docs

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/signup` | Create account |
| POST | `/api/auth/login` | Login with email/password |
| POST | `/api/auth/google` | Login with Google (Firebase token) |
| GET | `/api/auth/me` | Current user |
| GET | `/api/investigations` | List user's investigations |
| POST | `/api/investigations` | Create investigation |
| GET | `/api/investigations/{id}` | Get investigation |
| DELETE | `/api/investigations/{id}` | Delete investigation |
| GET | `/api/investigations/{id}/stream` | SSE: run agent pipeline |
| POST | `/api/evidence/upload/{id}` | Upload evidence files |
| GET | `/api/evidence/{id}` | List evidence |
| GET | `/api/agents/{id}` | Get agent run states |
| GET | `/api/reports/{id}` | Get final report |
| POST | `/api/chat/{id}` | Send follow-up question |
| GET | `/api/chat/{id}` | Get chat history |

---

## Agent Pipeline

```
Evidence Collector  →  indexes uploaded files
        ↓
Evidence Analyzer   →  extracts people, locations, dates, entities
        ↓
Timeline Builder    →  reconstructs chronological events (FACT/INFERRED/UNCONFIRMED)
        ↓
Evidence Linker     →  finds cross-evidence connections
        ↓
Subject Analyzer    →  profiles persons of interest (Involvement Indicator)
        ↓
Report Generator    →  assembles the final case report
```

---

## SSE Event Format

```json
{"event": "agent_started", "agent_id": "collector"}
{"event": "agent_activity", "agent_id": "collector", "line": {"kind": "ok", "text": "..."}}
{"event": "agent_output", "agent_id": "collector", "summary": ["3 items indexed"]}
{"event": "agent_completed", "agent_id": "collector"}
{"event": "agent_failed", "agent_id": "collector", "error": "..."}
{"event": "investigation_completed", "investigation_id": "inv_abc123"}
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Optional: Tesseract OCR

To enable image text extraction, install Tesseract:
- **Windows**: https://github.com/UB-Mannheim/tesseract/wiki
- **macOS**: `brew install tesseract`
- **Linux**: `sudo apt install tesseract-ocr`

---

## Notes

- All evidence files are stored in `uploads/{user_id}/{investigation_id}/`
- Investigation data is isolated per user — users can never see each other's cases
- Chroma Cloud vectors are filtered by `investigation_id` on every query
- If the page is refreshed during an active investigation, the agent state is recovered from MongoDB via `GET /api/agents/{id}`
