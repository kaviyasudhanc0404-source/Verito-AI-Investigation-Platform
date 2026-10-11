"""
main.py
-------
FastAPI application entry point for VERITO backend.
Registers all routers, configures CORS, and manages the MongoDB lifecycle.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, investigations, evidence, agents, reports, chat
from app.core.config import get_settings
from app.database.mongodb import connect, disconnect

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect to MongoDB on startup, disconnect on shutdown."""
    await connect()
    yield
    await disconnect()


app = FastAPI(
    title="VERITO API",
    description="AI Investigation Platform — Backend API",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
API_PREFIX = "/api"

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(investigations.router, prefix=API_PREFIX)
app.include_router(evidence.router, prefix=API_PREFIX)
app.include_router(agents.router, prefix=API_PREFIX)
app.include_router(reports.router, prefix=API_PREFIX)
app.include_router(chat.router, prefix=API_PREFIX)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    """Simple liveness check."""
    return {"status": "ok", "service": "VERITO API"}
