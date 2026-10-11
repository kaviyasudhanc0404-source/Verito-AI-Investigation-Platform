"""
Pipeline control flow, statuses and diagnostics, with MongoDB replaced by fakes.
"""

import asyncio

import pytest

from app.agents import base
from app.agents.base import AgentResult
from app.workflows import pipeline


class FakeCollection:
    def __init__(self) -> None:
        self.updates: list[tuple[dict, dict]] = []

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))


@pytest.fixture
def fake_db(monkeypatch):
    runs = FakeCollection()
    state = {"statuses": [], "diagnostics": None}
    monkeypatch.setattr(base.collections, "agent_runs", lambda: runs)

    async def set_status(_investigation_id, status):
        state["statuses"].append(status)

    async def get_investigation(_investigation_id):
        return {"title": "Untitled case"}

    async def update_investigation(_investigation_id, **_fields):
        return None

    async def update_report(_investigation_id, **fields):
        state["diagnostics"] = fields["diagnostics"]

    monkeypatch.setattr(pipeline.investigation_service, "update_investigation_status", set_status)
    monkeypatch.setattr(pipeline.investigation_service, "get_investigation", get_investigation)
    monkeypatch.setattr(pipeline.investigation_service, "update_investigation", update_investigation)
    monkeypatch.setattr(pipeline.report_service, "update_report", update_report)
    return state


def _stages(fail_at: str | None = None, fallback_at: str | None = None):
    async def step(ctx, agent, agent_id):
        await agent.log("ok", f"{agent_id} ran")
        if agent_id == fail_at:
            raise RuntimeError("boom")
        if agent_id == fallback_at:
            await agent.fallback("AI unavailable; used rules.")
        return AgentResult({"title": "A title"} if agent_id == "report" else {}, [agent_id], agent.status,
                           counts={"items": 1})

    return tuple((agent_id, agent_id.title(), lambda ctx, agent, a=agent_id: step(ctx, agent, a))
                 for agent_id in base.AGENT_IDS)


def _run(investigation_id: str):
    events: list[tuple[str, dict]] = []

    async def emit(event, payload):
        events.append((event, payload))

    status = asyncio.run(pipeline.run_investigation_pipeline(investigation_id, "usr_1", "", emit))
    return status, [name for name, _ in events]


def test_failed_step_skips_the_rest_and_marks_failure(fake_db, monkeypatch):
    monkeypatch.setattr(pipeline, "STAGES", _stages(fail_at="timeline"))
    status, names = _run("inv_1")
    assert status == "failed" and fake_db["statuses"] == ["processing", "failed"]
    assert names.count("agent_failed") == 1 and names.count("agent_blocked") == len(base.AGENT_IDS) - 3
    assert names[-1] == "investigation_completed"
    assert fake_db["diagnostics"] is None                     # no report, nothing to attach


def test_fallback_status_and_diagnostics_are_recorded(fake_db, monkeypatch):
    monkeypatch.setattr(pipeline, "STAGES", _stages(fallback_at="analyzer"))
    status, names = _run("inv_3")
    assert status == "complete" and names.count("agent_completed_with_fallback") == 1
    diagnostics = fake_db["diagnostics"]
    assert set(diagnostics) == set(base.AGENT_IDS)
    analyzer = diagnostics["analyzer"]
    assert analyzer["status"] == "completed_with_fallback" and analyzer["fallbacks"] == 1
    assert analyzer["counts"] == {"items": 1} and analyzer["llm"]["calls"] == 0 and analyzer["duration_ms"] >= 0


def test_reconnecting_subscribers_share_one_run(fake_db, monkeypatch):
    monkeypatch.setattr(pipeline, "STAGES", _stages())

    async def scenario():
        first = pipeline.subscribe("inv_2", "usr_1", "")
        second = pipeline.subscribe("inv_2", "usr_1", "")            # e.g. a page refresh
        assert pipeline.is_running("inv_2")
        received = []
        while (item := await second.get()) is not None:
            received.append(item[0])
        return first, received

    first, received = asyncio.run(scenario())
    assert received.count("agent_started") == len(base.AGENT_IDS) and received[-1] == "investigation_completed"
    assert not pipeline.is_running("inv_2") and fake_db["statuses"] == ["processing", "complete"]


def test_api_schema_lists_every_agent():
    import typing

    from app.schemas.agent import AgentRunResponse

    assert set(typing.get_args(AgentRunResponse.model_fields["id"].annotation)) == set(base.AGENT_IDS)
