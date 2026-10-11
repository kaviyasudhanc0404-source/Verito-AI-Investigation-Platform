"""
Shared test fixtures and helpers.

Fixture cases (tests/fixtures/<name>/) go through the real parsers and cleaner:
  meridian   procurement golden case — spreadsheet / PDF content stored as the
             text those parsers produce (.txt), raw CSV / JSON / EML parsed live
  cyber      VPN sign-in log, security alert, IT email, analyst notes
  conflicts  two invoice exports that disagree, a duplicate row, payments
  hr         complaint email, witness statement, training register

`run_downstream(case)` runs Timeline → Linker → Validator → Subject → Report
offline: no MongoDB, and the LLM disabled so the rule-based paths are exercised.
"""

import asyncio
from pathlib import Path

import pytest

from app.agents import evidence_linker, finding_validator, report_generator, subject_analyzer, timeline_builder
from app.agents.base import PipelineContext
from app.parsers import parse_bytes
from app.services.case_model import Case
from app.services.text_cleaner import clean

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
FIXTURES = FIXTURE_ROOT / "meridian"

# (evidence id, filename shown to users, fixture file)
_MERIDIAN = [
    ("E-001", "audit_findings.pdf", "audit_findings.txt"),
    ("E-002", "bank_transactions.csv", "bank_transactions.csv"),
    ("E-004", "chat_records.json", "chat_records.json"),
    ("E-005", "emails.eml", "emails.eml"),
    ("E-006", "employee_records.xlsx", "employee_records.txt"),
    ("E-007", "phone_records.csv", "phone_records.csv"),
    ("E-008", "procurement_invoices.pdf", "procurement_invoices.txt"),
    ("E-009", "procurement_records.xlsx", "procurement_records.txt"),
    ("E-010", "system_audit.json", "system_audit.json"),
    ("E-011", "vendor_records.xlsx", "vendor_records.txt"),
]


def content_of(path: Path) -> str:
    raw = path.read_text(encoding="utf-8") if path.suffix == ".txt" else parse_bytes(path.read_bytes(), path.name)
    return clean(raw)


def load_docs(name: str) -> list[dict]:
    """Evidence documents for a fixture folder, numbered E-001… in filename order."""
    paths = sorted((FIXTURE_ROOT / name).iterdir())
    return [{"evidence_id": f"E-{n:03d}", "filename": p.name, "content": content_of(p)} for n, p in enumerate(paths, 1)]


class FakeAgent:
    """Stands in for AgentRun: collects activity lines instead of writing to MongoDB."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.warnings = 0
        self.fallbacks = 0

    @property
    def status(self) -> str:
        return "completed_with_fallback" if self.fallbacks else "completed_with_warnings" if self.warnings else "done"

    async def log(self, kind: str, text: str) -> None:
        self.lines.append((kind, text))
        self.warnings += kind in {"warn", "error"}

    async def fallback(self, text: str, units: int = 1) -> None:
        self.fallbacks += units
        await self.log("warn", text)


async def _no_llm(*_args, **_kwargs):
    raise RuntimeError("LLM disabled in tests")


def run_downstream(case: Case, prompt: str = "") -> dict:
    """Run the agents after the analyzer, in pipeline order. Returns outputs + statuses + the saved report."""
    patch = pytest.MonkeyPatch()
    patch.setattr(timeline_builder, "chat_completion_json", _no_llm)
    patch.setattr(report_generator, "chat_completion_json", _no_llm)
    saved: dict = {}

    async def save(_investigation_id, report):
        saved.update(report)

    patch.setattr(report_generator, "save_report", save)
    ctx = PipelineContext("inv_test", "usr_test", prompt, emit=None, case=case)
    statuses: dict[str, str] = {}

    async def run_all():
        for agent_id, module in (("timeline", timeline_builder), ("linker", evidence_linker),
                                 ("validator", finding_validator), ("subject", subject_analyzer),
                                 ("report", report_generator)):
            result = await module.run(ctx, FakeAgent())
            ctx.outputs[agent_id], statuses[agent_id] = result.output, result.status

    try:
        asyncio.run(run_all())
    finally:
        patch.undo()
    return {**ctx.outputs, "statuses": statuses, "saved_report": saved, "observations": ctx.observations}


@pytest.fixture(scope="session")
def meridian_docs() -> list[dict]:
    return [{"evidence_id": eid, "filename": name, "content": content_of(FIXTURES / fixture)}
            for eid, name, fixture in _MERIDIAN]


@pytest.fixture(scope="session")
def meridian_case(meridian_docs) -> Case:
    return Case.build(meridian_docs, prose_people={"E-001": ["K. Balasubramanian"]})


@pytest.fixture(scope="session")
def cyber_case() -> Case:
    return Case.build(load_docs("cyber"))


@pytest.fixture(scope="session")
def conflicts_case() -> Case:
    return Case.build(load_docs("conflicts"))


@pytest.fixture(scope="session")
def hr_case() -> Case:
    return Case.build(load_docs("hr"))
