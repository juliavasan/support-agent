from __future__ import annotations

from unittest.mock import MagicMock

from conftest import INCIDENT_RECORD
from support_agent.graph.workflow import build_graph
from support_agent.servicenow import Incident, IncidentNotFoundError, ServiceNowAuthError


def graph_with(client: MagicMock):  # type: ignore[no-untyped-def]
    return build_graph(client_factory=lambda: client)


def test_graph_structure_is_linear() -> None:
    drawn = build_graph(client_factory=MagicMock).get_graph()
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges == {
        ("__start__", "retrieve_incident"),
        ("retrieve_incident", "format_incident"),
        ("format_incident", "__end__"),
    }


def test_successful_run_state_transitions() -> None:
    client = MagicMock()
    client.get_incident.return_value = Incident.from_api(INCIDENT_RECORD)
    graph = graph_with(client)

    updates = list(graph.stream({"incident_id": "INC0010001"}, stream_mode="updates"))

    assert [next(iter(u)) for u in updates] == ["retrieve_incident", "format_incident"]
    retrieved = updates[0]["retrieve_incident"]
    assert retrieved["error"] is None
    assert retrieved["incident"]["number"] == "INC0010001"
    assert "Assignment group  : AI Support Agent" in updates[1]["format_incident"]["display"]
    client.get_incident.assert_called_once_with("INC0010001")


def test_output_schema_only_exposes_public_fields() -> None:
    client = MagicMock()
    client.get_incident.return_value = Incident.from_api(INCIDENT_RECORD)

    result = graph_with(client).invoke({"incident_id": "INC0010001"})

    assert set(result) == {"incident", "error", "display"}
    assert result["incident"]["caller"] == "Amira Haddad"


def test_not_found_flows_to_formatted_error() -> None:
    client = MagicMock()
    client.get_incident.side_effect = IncidentNotFoundError("Incident INC9999999 not found")

    result = graph_with(client).invoke({"incident_id": "INC9999999"})

    assert result["incident"] is None
    assert result["error"] == "Incident INC9999999 not found"
    assert result["display"].startswith("ERROR (IncidentNotFoundError)")


def test_auth_error_flows_to_formatted_error() -> None:
    client = MagicMock()
    client.get_incident.side_effect = ServiceNowAuthError("Authentication failed (HTTP 401): x")

    result = graph_with(client).invoke({"incident_id": "INC0010001"})

    assert "ServiceNowAuthError" in result["display"]
