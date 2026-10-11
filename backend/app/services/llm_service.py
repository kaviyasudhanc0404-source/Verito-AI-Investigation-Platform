"""
services/llm_service.py
------------------------
Shared Groq client used by every agent and the chat endpoint.

Public API:
  chat_completion(messages, ...)       -> str
  chat_completion_json(messages, ...)  -> dict   (optionally validated by a Pydantic schema)
  transcribe_audio(file_path)          -> str
  track_usage()                        -> context manager collecting LLMUsage for one stage
  failure_reason(exc)                  -> short, credential-free reason for logs and reports
  estimate_tokens(text)                -> int

Reliability is handled here once, so agents never repeat it:
  • One client, created lazily, with explicit connect/read timeouts.
  • Reasoning models (e.g. openai/gpt-oss-*) spend hidden reasoning tokens from
    the same budget as the answer, so they get reasoning_effort plus headroom.
  • An empty or cut-off reply is retried once with a doubled budget.
  • Rate limits, timeouts, connection errors and 5xx responses are retried with
    backoff. Authentication and unknown-model errors fail immediately with a
    clear message — retrying cannot fix them.
  • Every call logs purpose, model, latency, token usage and outcome (never the
    API key or the evidence text).

Errors raised to callers:
  LLMConfigError        — bad API key, missing permission or unknown model
  ContextTooLargeError  — the request itself is too large (HTTP 413)
  LLMResponseError      — empty / invalid / schema-violating reply (subclass of ValueError)
  ServiceException      — any other provider error after retries
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

import groq
import httpx
from groq import AsyncGroq
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.exceptions import ServiceException

logger = logging.getLogger(__name__)
settings = get_settings()

_client: AsyncGroq | None = None

_REASONING_HEADROOM = 2048      # extra completion tokens for hidden reasoning
_MAX_COMPLETION_TOKENS = 12000  # hard ceiling for any single request
_TRANSIENT_ERRORS = (groq.RateLimitError, groq.APITimeoutError, groq.APIConnectionError, groq.InternalServerError)


class LLMConfigError(ServiceException):
    """The provider rejected the credentials or the configured model."""


class ContextTooLargeError(ServiceException):
    """The request payload exceeds the provider's per-request limit (HTTP 413)."""


class LLMResponseError(ValueError):
    """The model returned an empty, truncated, non-JSON or schema-violating reply."""


# ── Usage tracking ────────────────────────────────────────────────────────────

@dataclass
class LLMUsage:
    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"calls": self.calls, "failures": self.failures, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "errors": self.errors[:5]}


_usage: ContextVar[LLMUsage | None] = ContextVar("llm_usage", default=None)


@contextmanager
def track_usage() -> Iterator[LLMUsage]:
    """Collect LLM usage for everything awaited inside the block (one pipeline stage)."""
    usage = LLMUsage()
    token = _usage.set(usage)
    try:
        yield usage
    finally:
        _usage.reset(token)


def _record(prompt_tokens: int = 0, completion_tokens: int = 0, error: str = "") -> None:
    usage = _usage.get()
    if usage is None:
        return
    usage.calls += 1
    usage.prompt_tokens += prompt_tokens
    usage.completion_tokens += completion_tokens
    if error:
        usage.failures += 1
        if error not in usage.errors:
            usage.errors.append(error)


# ── Client ────────────────────────────────────────────────────────────────────

def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        if not settings.groq_api_key:
            raise LLMConfigError("GROQ_API_KEY is not configured.")
        _client = AsyncGroq(
            api_key=settings.groq_api_key,
            timeout=httpx.Timeout(settings.groq_timeout_seconds, connect=settings.groq_connect_timeout_seconds),
            max_retries=0,       # retries are handled below, with logging and classification
        )
    return _client


def estimate_tokens(text: str) -> int:
    """Rough token count (≈4 characters per token) for batch sizing."""
    return len(text or "") // 4 + 1


def _is_reasoning_model(model: str) -> bool:
    return "gpt-oss" in model or "qwen3" in model or "deepseek-r1" in model


def _request_kwargs(
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    reasoning_effort: str = "low",
) -> dict[str, Any]:
    model = settings.groq_model
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if _is_reasoning_model(model):
        kwargs["max_tokens"] = min(max_tokens + _REASONING_HEADROOM, _MAX_COMPLETION_TOKENS)
        kwargs["reasoning_effort"] = reasoning_effort
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return kwargs


# ── Requests ──────────────────────────────────────────────────────────────────

async def _create(kwargs: dict[str, Any], purpose: str) -> tuple[str, str]:
    """Send one request, retrying transient errors. Returns (content, finish_reason)."""
    attempts = settings.groq_max_attempts
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            response = await _get_client().chat.completions.create(**kwargs)
        except Exception as exc:
            error = _classify(exc)
            reason = failure_reason(error)
            _log_call(purpose, kwargs, started, outcome=f"error: {reason}", attempt=attempt)
            _record(error=reason)
            if isinstance(exc, _TRANSIENT_ERRORS) and attempt < attempts:
                delay = _retry_after(exc) or min(2 ** (attempt + 1), 30)
                await asyncio.sleep(delay)
                continue
            raise error from exc
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        _log_call(purpose, kwargs, started, outcome=str(choice.finish_reason or "ok"), attempt=attempt,
                  tokens=(prompt_tokens, completion_tokens))
        _record(prompt_tokens, completion_tokens)
        return (choice.message.content or "").strip(), str(choice.finish_reason or "")
    raise ServiceException("LLM request failed.")


def _classify(exc: Exception) -> Exception:
    """Map a provider exception to the error callers should see."""
    if isinstance(exc, LLMConfigError):
        return exc
    message = _redact(str(exc))
    lowered = message.lower()
    if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
        return LLMConfigError(f"Groq rejected the API key or its permissions — check GROQ_API_KEY. ({message})")
    if isinstance(exc, groq.NotFoundError) or "model_not_found" in lowered:
        return LLMConfigError(f"Groq model '{settings.groq_model}' is not available — check GROQ_MODEL. ({message})")
    if "413" in lowered or "too large" in lowered:
        return ContextTooLargeError(message)
    if "json_validate_failed" in lowered or "failed to generate json" in lowered:
        return LLMResponseError(message)
    if isinstance(exc, groq.APITimeoutError):
        return ServiceException(f"LLM request timed out after {settings.groq_timeout_seconds:g}s.")
    if isinstance(exc, groq.RateLimitError):
        return ServiceException(f"LLM rate limit persisted: {message}")
    return ServiceException(f"LLM request failed: {message}")


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    json_mode: bool = False,
    reasoning_effort: str = "low",
    purpose: str = "chat",
) -> str:
    """Return the model's text reply, retrying once if it comes back empty or cut off.

    reasoning_effort ("low" | "medium" | "high") only applies to reasoning models.
    purpose names the caller in logs and usage counters (e.g. "analyzer.extract").
    """
    budget = max_tokens
    content = ""
    for attempt in (1, 2):
        content, finish_reason = await _create(
            _request_kwargs(messages, temperature, budget, json_mode, reasoning_effort), purpose
        )
        if content and finish_reason != "length":
            return content
        logger.warning("LLM reply %s (purpose=%s, finish_reason=%s, budget=%d, attempt=%d)",
                       "empty" if not content else "truncated", purpose, finish_reason, budget, attempt)
        budget = min(budget * 2, _MAX_COMPLETION_TOKENS)
    if content:
        return content
    raise LLMResponseError("LLM returned an empty reply.")


async def chat_completion_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    reasoning_effort: str = "low",
    purpose: str = "json",
    schema: type[BaseModel] | None = None,
) -> dict[str, Any]:
    """Return the model's reply parsed as a JSON object (validated against schema if given)."""
    options = {"temperature": temperature, "max_tokens": max_tokens,
               "reasoning_effort": reasoning_effort, "purpose": purpose}
    try:
        text = await chat_completion(messages, json_mode=True, **options)
    except LLMResponseError:
        # Provider-side JSON validation can reject a valid answer; ask once
        # more without JSON mode and parse locally.
        text = await chat_completion(_with_json_instruction(messages), **options)
    value = parse_json_object(text)
    if schema is None:
        return value
    try:
        return schema.model_validate(value).model_dump()
    except ValidationError as exc:
        _record(error="reply did not match the expected schema")
        raise LLMResponseError(f"LLM reply did not match the expected schema: {exc.error_count()} error(s).") from exc


async def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio file with Groq Whisper."""
    started = time.perf_counter()
    kwargs = {"model": settings.groq_whisper_model}
    try:
        with open(file_path, "rb") as handle:
            transcription = await _get_client().audio.transcriptions.create(
                file=(file_path, handle.read()), model=settings.groq_whisper_model, response_format="text",
            )
    except Exception as exc:
        error = _classify(exc)
        _log_call("transcribe", kwargs, started, outcome=f"error: {failure_reason(error)}", attempt=1)
        raise error from exc
    _log_call("transcribe", kwargs, started, outcome="ok", attempt=1)
    return str(transcription)


# ── JSON parsing ──────────────────────────────────────────────────────────────

def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model reply, tolerating fences and trailing commas."""
    candidate = _extract_json_object(text)
    for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
        try:
            value = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise LLMResponseError(f"LLM returned invalid JSON. Start of reply: {text[:160]!r}")


def _extract_json_object(text: str) -> str:
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start : end + 1] if 0 <= start < end else stripped


def _with_json_instruction(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    patched = [dict(message) for message in messages]
    patched[-1]["content"] = f"{patched[-1].get('content', '')}\n\nReturn exactly one valid JSON object and nothing else."
    return patched


# ── Helpers ───────────────────────────────────────────────────────────────────

def failure_reason(exc: BaseException) -> str:
    """A short, credential-free description of why an LLM call failed."""
    if isinstance(exc, LLMConfigError):
        return "configuration error (API key or model)"
    if isinstance(exc, ContextTooLargeError):
        return "request too large"
    if isinstance(exc, LLMResponseError):
        return "invalid or empty reply"
    detail = getattr(exc, "detail", "") or str(exc)
    if "timed out" in detail:
        return "timeout"
    if "rate limit" in detail.lower():
        return "rate limit"
    return "provider error"


def _log_call(purpose: str, kwargs: dict, started: float, *, outcome: str, attempt: int,
              tokens: tuple[int, int] = (0, 0)) -> None:
    logger.info("llm purpose=%s model=%s attempt=%d latency_ms=%d prompt_tokens=%d completion_tokens=%d outcome=%s",
                purpose, kwargs.get("model"), attempt, (time.perf_counter() - started) * 1000, *tokens, outcome)


def _retry_after(exc: Exception) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return min(float(headers.get("retry-after", "")), 30.0)
    except ValueError:
        return None


def _redact(text: str) -> str:
    """Remove API keys and bearer tokens from provider error messages."""
    text = re.sub(r"gsk_[A-Za-z0-9_\-]+", "[redacted]", text)
    return re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+", r"\1=[redacted]", text)[:300]
