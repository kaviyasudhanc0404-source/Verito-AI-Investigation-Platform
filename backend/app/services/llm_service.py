"""
services/llm_service.py
------------------------
Shared Groq LLM client used by all six investigation agents.
All agents import `chat_completion`, `chat_completion_json`, and
`transcribe_audio` from here.

Error handling contract:
  • ContextTooLargeError  — raised on 413 (payload too large for one request)
  • ValueError            — raised on 400 JSON validation failures
  • ServiceException      — raised on misconfiguration
  • Groq RateLimitError   — propagated as-is (agents handle exponential backoff)
  • All other Groq errors — re-raised as ServiceException with original message

This module does NOT retry or truncate — that is each agent's responsibility.
Every error preserves the original provider message so agents can log it.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from groq import AsyncGroq
from groq import RateLimitError as GroqRateLimitError

from app.core.config import get_settings
from app.core.exceptions import ServiceException

logger = logging.getLogger(__name__)
settings = get_settings()

_client: AsyncGroq | None = None


# ── Custom exceptions ─────────────────────────────────────────────────────────

class ContextTooLargeError(ServiceException):
    """Raised when a request payload exceeds the provider's per-request token limit.

    This is a 413 error — it means the single request is too large.
    It is NOT related to TPM (tokens per minute), which is a rate-limit concept.

    Attributes:
        estimated_tokens: Token count estimated before the request was sent.
        original_message: Original provider error message (never hidden).
    """

    def __init__(
        self,
        estimated_tokens: int = 0,
        original_message: str = "",
    ) -> None:
        self.estimated_tokens = estimated_tokens
        self.original_message = original_message
        super().__init__(
            f"Request payload too large (~{estimated_tokens} estimated tokens). "
            f"Original: {original_message or 'no detail'}"
        )


# ── Telemetry helper ──────────────────────────────────────────────────────────

def emit_llm_failure_telemetry(
    *,
    agent_name: str,
    error_code: int | str,
    error_category: str,
    estimated_input_tokens: int,
    requested_output_tokens: int,
    payload_size_chars: int,
    attempt_number: int = 1,
    batch_number: int | None = None,
    batch_total: int | None = None,
    evidence_ids: list[str] | None = None,
    chunk_indices: list[str] | None = None,
    model_name: str | None = None,
    was_retried: bool = False,
    retry_action: str | None = None,
    fallback_action: str | None = None,
    original_message: str = "",
) -> None:
    """Emit a structured telemetry log entry for an LLM failure.

    Every LLM failure (400, 413, 429, timeout) must call this function.
    No API keys or sensitive credentials are included — only metadata.

    The log is emitted at WARNING level via Python's logging system.
    It is not sent to the frontend as an SSE event.
    """
    batch_str = (
        f"{batch_number}/{batch_total}" if batch_number is not None and batch_total is not None
        else str(batch_number) if batch_number is not None
        else "N/A"
    )
    evidence_str = ",".join(evidence_ids) if evidence_ids else "N/A"
    chunks_str = ",".join(chunk_indices) if chunk_indices else "N/A"
    model_str = model_name or settings.groq_model

    # Redact any credential-like strings from the original message before logging
    safe_msg = _redact_credentials(original_message)[:200] if original_message else ""

    logger.warning(
        "[TELEMETRY] LLM failure\n"
        "  agent=%s  batch=%s  attempt=%d  model=%s\n"
        "  evidence_ids=%s  chunk_indices=%s\n"
        "  status=%s  category=%s\n"
        "  estimated_input_tokens=%d  requested_output_tokens=%d  payload_chars=%d\n"
        "  retry=%s  retry_action=%s  fallback=%s\n"
        "  original=%s",
        agent_name, batch_str, attempt_number, model_str,
        evidence_str, chunks_str,
        error_code, error_category,
        estimated_input_tokens, requested_output_tokens, payload_size_chars,
        was_retried, retry_action or "None", fallback_action or "None",
        safe_msg,
    )


def _redact_credentials(text: str) -> str:
    """Remove API keys and bearer tokens from error messages before logging."""
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|token)\s*[:=]\s*['\"]?[^\s'\",.}]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"gsk_[A-Za-z0-9_\-]+", "[redacted_groq_key]", text)
    return text


def _classify_error(exc: Exception) -> tuple[int | str, str]:
    """Return (error_code, error_category) for a provider exception."""
    msg = str(exc).lower()
    if "413" in msg or "request too large" in msg or "payload too large" in msg:
        return 413, "context_too_large"
    if "400" in msg or "json" in msg or "failed to validate" in msg:
        return 400, "json_validation"
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return 429, "rate_limit"
    if "timeout" in msg or "timed out" in msg:
        return "timeout", "timeout"
    return "unknown", "unknown"


# ── LLM client ────────────────────────────────────────────────────────────────

def _get_client() -> AsyncGroq:
    """Return a lazily-created Groq client."""
    global _client
    if _client is None:
        if not settings.groq_api_key:
            raise ServiceException("GROQ_API_KEY is not configured.")
        _client = AsyncGroq(api_key=settings.groq_api_key)
    return _client


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    response_format: Optional[dict[str, Any]] = None,
) -> str:
    """Send a chat completion request to Groq and return the response text.

    Raises:
        ContextTooLargeError: On 413 — payload exceeds provider per-request limit.
        ValueError: On 400 — JSON validation failure or malformed response.
        GroqRateLimitError: On 429 — propagated; agents handle backoff.
        ServiceException: On other provider errors.
    """
    kwargs: dict[str, Any] = {
        "model": settings.groq_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    try:
        response = await _get_client().chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""
    except GroqRateLimitError:
        raise  # Agents handle 429 with exponential backoff
    except Exception as exc:
        original = str(exc)
        code, category = _classify_error(exc)
        if code == 413 or category == "context_too_large":
            raise ContextTooLargeError(
                estimated_tokens=0,  # Caller may fill in estimate
                original_message=original,
            ) from exc
        if code == 400 or category == "json_validation":
            # Convert Groq BadRequestError / JSON validation errors to ValueError
            # so agents' `except ValueError` retry blocks trigger correctly.
            raise ValueError(f"LLM 400 error: {original}") from exc
        raise


async def chat_completion_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Like chat_completion but forces JSON output and parses it.

    Raises:
        ContextTooLargeError: On 413.
        ValueError: On 400 or invalid JSON in response.
        GroqRateLimitError: On 429.
        ServiceException: On other provider errors.
    """
    text = await chat_completion(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}\nRaw: {text[:200]}") from e


async def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio file using Groq Whisper.
    Returns the transcribed text.
    """
    with open(file_path, "rb") as f:
        transcription = await _get_client().audio.transcriptions.create(
            file=(file_path, f.read()),
            model=settings.groq_whisper_model,
            response_format="text",
        )
    return str(transcription)
