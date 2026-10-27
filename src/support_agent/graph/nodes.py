"""Graph nodes. Each node takes the state and returns a partial state update."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from support_agent.graph.state import IncidentState
from support_agent.servicenow import ServiceNowClient, ServiceNowError

ClientFactory = Callable[[], ServiceNowClient]

DISPLAY_FIELDS = (
    ("Number", "number"),
    ("Short description", "short_description"),
    ("Priority", "priority"),
    ("State", "state"),
    ("Category", "category"),
    ("Assignment group", "assignment_group"),
    ("Assigned to", "assigned_to"),
    ("Caller", "caller"),
    ("Opened", "opened_at"),
    ("sys_id", "sys_id"),
)


def make_retrieve_incident(client_factory: ClientFactory) -> Callable[[IncidentState], dict[str, Any]]:
    """Build the retrieval node. The client is injected so tests can pass a mock."""

    def retrieve_incident(state: IncidentState) -> dict[str, Any]:
        try:
            incident = client_factory().get_incident(state["incident_id"])
        except ServiceNowError as exc:
            return {"incident": None, "error": str(exc), "error_type": type(exc).__name__}
        return {"incident": incident.model_dump(), "error": None, "error_type": None}

    return retrieve_incident


def format_incident(state: IncidentState) -> dict[str, Any]:
    """Render the incident (or the error) as plain text for the terminal and Studio."""
    if state.get("error"):
        return {"display": f"ERROR ({state.get('error_type')}): {state['error']}"}

    incident = state.get("incident") or {}
    width = max(len(label) for label, _ in DISPLAY_FIELDS)
    lines = [f"{label:<{width}} : {incident.get(key) or '-'}" for label, key in DISPLAY_FIELDS]
    description = (incident.get("description") or "-").strip()
    lines += ["", "Description:", *[f"  {line}" for line in description.splitlines()]]
    return {"display": "\n".join(lines)}
