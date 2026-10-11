"""
Live Groq connectivity check — opt-in, never part of the default run.

    RUN_GROQ_INTEGRATION=1 ./venv/Scripts/python.exe -m pytest -m integration

Uses GROQ_API_KEY / GROQ_MODEL from .env and sends one tiny request.
"""

import asyncio
import os

import pytest

from app.services import llm_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_GROQ_INTEGRATION") != "1", reason="set RUN_GROQ_INTEGRATION=1 to call Groq"),
]


def test_groq_returns_valid_json():
    async def call():
        with llm_service.track_usage() as usage:
            reply = await llm_service.chat_completion_json(
                [{"role": "user", "content": 'Reply with the JSON object {"status": "ok"} and nothing else.'}],
                max_tokens=50, purpose="integration.check",
            )
        return reply, usage

    reply, usage = asyncio.run(call())
    assert reply.get("status") == "ok"
    assert usage.calls >= 1 and usage.completion_tokens > 0
