"""
services/llm_service.py
------------------------
Shared Groq LLM client used by all six investigation agents.
All agents import `chat_completion` and `transcribe_audio` from here.
"""

import json
from typing import Any, Optional

from groq import AsyncGroq

from app.core.config import get_settings
from app.core.exceptions import ServiceException

settings = get_settings()

_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    """Return a lazily-created Groq client.

    Keeping this lazy lets the API import and expose non-LLM routes before the
    user has filled in backend/.env.
    """
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
    """
    Send a chat completion request to Groq and return the response text.

    Args:
        messages: List of role/content dicts (system, user, assistant).
        temperature: Sampling temperature (lower = more deterministic).
        max_tokens: Maximum tokens in the response.
        response_format: Optional dict e.g. {"type": "json_object"}.
    """
    kwargs: dict[str, Any] = {
        "model": settings.groq_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    response = await _get_client().chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


async def chat_completion_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """
    Like chat_completion but forces JSON output and parses it.
    Raises ValueError if the response is not valid JSON.
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
        raise ValueError(f"LLM returned invalid JSON: {e}\nRaw: {text}") from e


async def transcribe_audio(file_path: str) -> str:
    """
    Transcribe an audio file using Groq Whisper.
    Returns the transcribed text.
    """
    with open(file_path, "rb") as f:
        transcription = await _get_client().audio.transcriptions.create(
            file=(file_path, f.read()),
            model=settings.groq_whisper_model,
            response_format="text",
        )
    return str(transcription)
