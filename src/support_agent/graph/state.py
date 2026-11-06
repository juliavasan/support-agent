"""Typed LangGraph state for the incident workflow."""

from __future__ import annotations

from typing import Any, Literal, NotRequired

# typing_extensions.TypedDict (not typing.TypedDict): on Python < 3.12 Pydantic can only
# build the JSON schema that LangSmith Studio uses for its input form from this variant.
from typing_extensions import TypedDict

Mode = Literal["dry_run", "write"]


class InputState(TypedDict):
    """What a caller (CLI or LangSmith Studio) provides."""

    incident_id: str  # incident number (INC0010001) or 32-character sys_id
    # "dry_run" proposes the work note without writing. "write" appends it, but only for
    # designated test incidents (see guardrails/write_policy.py). If omitted, the
    # DEFAULT_RUN_MODE setting applies (dry_run unless configured otherwise).
    mode: NotRequired[Mode]


class OutputState(TypedDict, total=False):
    """What the graph returns."""

    incident: dict[str, Any] | None
    context: dict[str, Any] | None  # RetrievalResult: KB articles, known issues, similar incidents
    assessment: dict[str, Any] | None  # TriageAssessment
    work_note: str | None  # the exact text proposed or written
    write_result: dict[str, Any] | None  # status, verification, journal entry
    error: str | None
    display: str


class IncidentState(InputState, OutputState, total=False):
    """Full internal state passed between nodes."""

    error_type: str | None
