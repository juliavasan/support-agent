"""Typed models for ServiceNow records."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

# Fields requested from the Table API. Display values are requested, so reference
# fields (assignment_group, assigned_to, caller_id, company) come back as names, not sys_ids.
INCIDENT_FIELDS = (
    "sys_id",
    "number",
    "short_description",
    "description",
    "priority",
    "impact",
    "urgency",
    "state",
    "category",
    "assignment_group",
    "assigned_to",
    "caller_id",
    "company",
    # Dot-walked: the caller's company, used when the incident's own Company is empty.
    "caller_id.company",
    "opened_at",
)

# Fields the agent must never change. Write mode compares them before and after a write.
PROTECTED_FIELDS = (
    "priority",
    "impact",
    "urgency",
    "category",
    "assignment_group",
    "assigned_to",
    "state",
    "company",
)

# One entry header in a journal field's display value, e.g.
# "2026-11-05 16:33:09 - Support Agent (Work notes)". Times are in the reading user's
# time zone and the author is a display name.
JOURNAL_HEADER_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (.+?) \(Work notes\)$", re.MULTILINE
)
INDEX_INCIDENT_FIELDS = (
    "sys_id",
    "number",
    "short_description",
    "description",
    "state",
    "close_code",
    "close_notes",
    "sys_updated_on",
)


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name) or ""
    # With sysparm_exclude_reference_link=false, references are {"display_value": ...}.
    if isinstance(value, dict):
        value = value.get("display_value", "")
    return str(value)


class Incident(BaseModel):
    sys_id: str
    number: str
    short_description: str = ""
    description: str = ""
    priority: str = ""
    impact: str = ""
    urgency: str = ""
    state: str = ""
    category: str = ""
    assignment_group: str = ""
    assigned_to: str = ""
    caller: str = ""
    company: str = ""  # the customer account; drives the customer-tier priority rule
    opened_at: str = ""

    @classmethod
    def from_api(cls, record: dict[str, Any]) -> Incident:
        return cls(
            sys_id=_text(record, "sys_id"),
            number=_text(record, "number"),
            short_description=_text(record, "short_description"),
            description=_text(record, "description"),
            priority=_text(record, "priority"),
            impact=_text(record, "impact"),
            urgency=_text(record, "urgency"),
            state=_text(record, "state"),
            category=_text(record, "category"),
            assignment_group=_text(record, "assignment_group"),
            assigned_to=_text(record, "assigned_to"),
            caller=_text(record, "caller_id"),
            # The customer account. ServiceNow's form fills incident.company from the
            # caller, but API-created incidents often leave it empty, so fall back to
            # the caller's company. Both come from records, never from ticket text.
            company=_text(record, "company") or _text(record, "caller_id.company"),
            opened_at=_text(record, "opened_at"),
        )

    def protected_values(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in PROTECTED_FIELDS}


class WorkNote(BaseModel):
    """One internal work-note entry, as shown on the incident."""

    value: str = ""
    created_on: str = ""  # reader's time zone
    created_by: str = ""  # display name


def parse_work_notes(journal: str) -> list[WorkNote]:
    """Split a work_notes display value (newest first) into entries."""
    headers = list(JOURNAL_HEADER_RE.finditer(journal))
    notes = []
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(journal)
        notes.append(
            WorkNote(
                value=journal[header.end() : end].strip(),
                created_on=header.group(1),
                created_by=header.group(2),
            )
        )
    return notes


class RecordVersion(BaseModel):
    """The minimum needed for an incremental sync: identity and last-updated time."""

    sys_id: str
    updated_on: str

    @classmethod
    def from_api(cls, record: dict[str, Any]) -> RecordVersion:
        return cls(sys_id=_text(record, "sys_id"), updated_on=_text(record, "sys_updated_on"))


class IndexIncident(BaseModel):
    """An incident as indexed for similar-ticket search."""

    sys_id: str
    number: str
    short_description: str = ""
    description: str = ""
    state: str = ""
    close_code: str = ""
    close_notes: str = ""
    updated_on: str = ""

    @classmethod
    def from_api(cls, record: dict[str, Any]) -> IndexIncident:
        return cls(
            sys_id=_text(record, "sys_id"),
            number=_text(record, "number"),
            short_description=_text(record, "short_description"),
            description=_text(record, "description"),
            state=_text(record, "state"),
            close_code=_text(record, "close_code"),
            close_notes=_text(record, "close_notes"),
            updated_on=_text(record, "sys_updated_on"),
        )
