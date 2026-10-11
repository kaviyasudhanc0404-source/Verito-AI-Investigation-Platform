"""
LLM reliability with the Groq client mocked: success, empty replies, authentication
failure, unknown model, timeout, rate limit, malformed and schema-violating replies,
usage tracking, and the analyzer's fallback when the LLM is unavailable.
"""

import asyncio
from types import SimpleNamespace

import groq
import httpx
import pytest
from pydantic import BaseModel

from app.agents import evidence_analyzer
from app.core.exceptions import ServiceException
from app.services import llm_service
from tests.conftest import FakeAgent

_REQUEST = httpx.Request("POST", "https://api.groq.example/openai/v1/chat/completions")
_MESSAGES = [{"role": "user", "content": "hi"}]


def _reply(content: str, finish_reason: str = "stop", tokens: tuple[int, int] = (10, 5)):
    usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1])
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
                           usage=usage)


def _status_error(cls, status: int, message: str, headers: dict | None = None):
    return cls(message, response=httpx.Response(status, request=_REQUEST, headers=headers or {}), body=None)


@pytest.fixture
def client(monkeypatch):
    """Install a fake Groq client whose create() plays back `client.script` (replies or exceptions)."""
    fake = SimpleNamespace(script=[], calls=[])

    async def create(**kwargs):
        fake.calls.append(kwargs)
        item = fake.script.pop(0) if len(fake.script) > 1 else fake.script[0]
        if isinstance(item, Exception):
            raise item
        return item

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(llm_service, "_get_client",
                        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(llm_service.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(llm_service.settings, "groq_model", "openai/gpt-oss-20b")
    monkeypatch.setattr(llm_service.settings, "groq_max_attempts", 3)
    return fake


def test_success_uses_native_reasoning_effort_and_json_mode(client):
    client.script = [_reply('{"ok": true}')]
    assert asyncio.run(llm_service.chat_completion_json(_MESSAGES, max_tokens=300)) == {"ok": True}
    sent = client.calls[0]
    assert sent["reasoning_effort"] == "low" and sent["response_format"] == {"type": "json_object"}
    assert "extra_body" not in sent


def test_empty_reply_is_retried_with_a_bigger_budget(client):
    client.script = [_reply(""), _reply('{"ok": true}')]
    assert asyncio.run(llm_service.chat_completion_json(_MESSAGES, max_tokens=300)) == {"ok": True}
    budgets = [call["max_tokens"] for call in client.calls]
    assert budgets[1] > budgets[0] >= 300 + 2048                          # reasoning headroom, then doubled


def test_authentication_failure_is_not_retried(client):
    client.script = [_status_error(groq.AuthenticationError, 401, "Invalid API Key gsk_secretvalue123")]
    with pytest.raises(llm_service.LLMConfigError) as error:
        asyncio.run(llm_service.chat_completion(_MESSAGES))
    assert len(client.calls) == 1
    assert "GROQ_API_KEY" in error.value.detail and "gsk_secretvalue123" not in error.value.detail   # redacted


def test_unknown_model_is_a_configuration_error(client):
    client.script = [_status_error(groq.NotFoundError, 404, "The model `nope` does not exist")]
    with pytest.raises(llm_service.LLMConfigError) as error:
        asyncio.run(llm_service.chat_completion(_MESSAGES))
    assert "GROQ_MODEL" in error.value.detail and len(client.calls) == 1


def test_timeout_is_retried_then_reported(client):
    client.script = [groq.APITimeoutError(request=_REQUEST)]
    with pytest.raises(ServiceException) as error:
        asyncio.run(llm_service.chat_completion(_MESSAGES))
    assert len(client.calls) == 3 and "timed out" in error.value.detail
    assert llm_service.failure_reason(error.value) == "timeout"


def test_rate_limit_is_retried_until_it_succeeds(client):
    client.script = [_status_error(groq.RateLimitError, 429, "Rate limit reached", {"retry-after": "1"}), _reply("done")]
    assert asyncio.run(llm_service.chat_completion(_MESSAGES)) == "done"
    assert len(client.calls) == 2


def test_malformed_and_schema_violating_replies_raise(client):
    client.script = [_reply("not json at all")]
    with pytest.raises(llm_service.LLMResponseError):
        asyncio.run(llm_service.chat_completion_json(_MESSAGES))

    class Expected(BaseModel):
        events: list[int]

    client.script = [_reply('{"events": ["x"]}')]
    with pytest.raises(llm_service.LLMResponseError):
        asyncio.run(llm_service.chat_completion_json(_MESSAGES, schema=Expected))


def test_usage_is_tracked_per_stage(client):
    client.script = [_reply("one", tokens=(100, 20))]

    async def two_calls():
        with llm_service.track_usage() as usage:
            await llm_service.chat_completion(_MESSAGES, purpose="test")
            await llm_service.chat_completion(_MESSAGES, purpose="test")
        return usage

    usage = asyncio.run(two_calls())
    assert usage.to_dict() == {"calls": 2, "failures": 0, "prompt_tokens": 200, "completion_tokens": 40, "errors": []}


def test_json_parsing_tolerates_fences_and_trailing_commas():
    assert llm_service.parse_json_object('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    with pytest.raises(llm_service.LLMResponseError):
        llm_service.parse_json_object("")


def test_analyzer_falls_back_to_rules_and_counts_it(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise ServiceException("LLM request timed out after 60s.")

    monkeypatch.setattr(evidence_analyzer, "chat_completion_json", unavailable)
    chunks = [{"evidence_id": "E-001", "chunk_index": 0,
               "text": "Payment for INV-2026-0101 was approved on 03-Feb-2026 by the finance lead. Thanks."}]
    agent = FakeAgent()
    extracted, fallback = asyncio.run(evidence_analyzer._analyse_narrative(chunks, agent))
    assert fallback == 1 and agent.fallbacks == 1 and agent.status == "completed_with_fallback"
    assert extracted["E-001"]["method"] == "rules" and extracted["E-001"]["s"]
    assert "timeout" in agent.lines[-1][1]
