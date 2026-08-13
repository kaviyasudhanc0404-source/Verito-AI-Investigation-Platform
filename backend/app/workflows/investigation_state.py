"""
workflows/investigation_state.py
----------------------------------
TypedDict defining the LangGraph state shared between all six agents.
Each agent reads from and writes to this state as it progresses.
"""

from typing import Any, TypedDict


class InvestigationState(TypedDict):
    """
    Shared state passed through the LangGraph investigation pipeline.
    Each agent populates its output field.
    """

    # Inputs
    investigation_id: str
    user_id: str
    investigation_prompt: str

    # Agent outputs (populated as the pipeline progresses)
    collector_output: dict[str, Any]    # Evidence Collector
    analyzer_output: dict[str, Any]     # Evidence Analyzer
    timeline_output: dict[str, Any]     # Timeline Builder
    linker_output: dict[str, Any]       # Evidence Linker
    subject_output: dict[str, Any]      # Subject Analyzer
    report_output: dict[str, Any]       # Report Generator

    # Error tracking
    error: str | None
