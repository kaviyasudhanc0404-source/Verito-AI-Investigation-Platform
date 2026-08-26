"""
core/config.py
--------------
Centralised settings loaded from .env via Pydantic BaseSettings.
All other modules import from here — never read os.environ directly.
"""

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # MongoDB Atlas
    mongodb_uri: str
    mongodb_database: str = "verito"

    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    # Groq
    groq_api_key: str
    groq_model: str = "openai/gpt-oss-20b"
    groq_whisper_model: str = "whisper-large-v3"

    # Gemini Vision API
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_timeout_seconds: int = 30
    gemini_max_image_dimension: int = 1600

    # Qdrant Cloud
    qdrant_url: str
    qdrant_api_key: str
    qdrant_collection: str = "verito_evidence"

    # Firebase
    firebase_project_id: str
    firebase_client_email: str
    firebase_private_key: str

    # CORS
    frontend_url: str = "http://localhost:3000"

    # File Upload
    upload_dir: str = "uploads"
    max_upload_mb: int = 50

    # ── Token Budget ─────────────────────────────────────────────────────────
    # These control per-REQUEST sizing, NOT provider TPM/rate limits.
    # TPM = how many tokens per minute the provider allows across ALL requests.
    # Per-request limit = how large a single request payload may be.
    # These are two separate constraints; do not conflate them.
    #
    # Derived effective_input_budget =
    #   llm_per_request_max_tokens
    #   - llm_system_prompt_reserve
    #   - llm_default_output_reserve
    #   - llm_safety_margin
    #
    # Conservative defaults suitable for Groq free-tier observed endpoint limits.
    # Adjust via .env for higher tiers or other providers.
    llm_per_request_max_tokens: int = 16384   # Per-request ceiling (not model ctx window)
    llm_system_prompt_reserve: int = 600      # Tokens reserved for system prompt
    llm_default_output_reserve: int = 2000    # Default output token reservation
    llm_safety_margin: int = 300              # Estimation error buffer
    # → default effective_input_budget = 16384 - 600 - 2000 - 300 = 13484

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()  # type: ignore[call-arg]
