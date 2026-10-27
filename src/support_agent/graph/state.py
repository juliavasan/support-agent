"""Typed LangGraph state for the incident workflow."""

from __future__ import annotations

from typing import Any

# typing_extensions.TypedDict (not typing.TypedDict): on Python < 3.12 Pydantic can only
# build the JSON schema that LangSmith Studio uses for its input form from this variant.
from typing_extensions import TypedDict


class InputState(TypedDict):
    """What a caller (CLI or LangSmith Studio) provides."""

    incident_id: str  # incident number (INC0010001) or 32-character sys_id


class OutputState(TypedDict, total=False):
    """What the graph returns."""

    incident: dict[str, Any] | None
    error: str | None
    display: str


class IncidentState(InputState, OutputState, total=False):
    """Full internal state passed between nodes."""

    error_type: str | None
