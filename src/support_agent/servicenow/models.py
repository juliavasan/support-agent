"""Typed models for ServiceNow records."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Fields requested from the Table API. Display values are requested, so reference
# fields (assignment_group, assigned_to, caller_id) come back as names, not sys_ids.
INCIDENT_FIELDS = (
    "sys_id",
    "number",
    "short_description",
    "description",
    "priority",
    "state",
    "category",
    "assignment_group",
    "assigned_to",
    "caller_id",
    "opened_at",
)


class Incident(BaseModel):
    sys_id: str
    number: str
    short_description: str = ""
    description: str = ""
    priority: str = ""
    state: str = ""
    category: str = ""
    assignment_group: str = ""
    assigned_to: str = ""
    caller: str = ""
    opened_at: str = ""

    @classmethod
    def from_api(cls, record: dict[str, Any]) -> Incident:
        def text(name: str) -> str:
            value = record.get(name) or ""
            # With sysparm_exclude_reference_link=false, references are {"display_value": ...}.
            if isinstance(value, dict):
                value = value.get("display_value", "")
            return str(value)

        return cls(
            sys_id=text("sys_id"),
            number=text("number"),
            short_description=text("short_description"),
            description=text("description"),
            priority=text("priority"),
            state=text("state"),
            category=text("category"),
            assignment_group=text("assignment_group"),
            assigned_to=text("assigned_to"),
            caller=text("caller_id"),
            opened_at=text("opened_at"),
        )
