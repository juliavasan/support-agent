from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from conftest import INCIDENT_RECORD, SYS_ID, make_response
from support_agent.servicenow import (
    IncidentNotFoundError,
    InvalidIdentifierError,
    ServiceNowAuthError,
    ServiceNowClient,
    ServiceNowError,
    ServiceNowPermissionError,
)


def test_get_by_number_queries_by_number(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(200, {"result": [INCIDENT_RECORD]})

    incident = client.get_incident("inc0010001")

    assert incident.number == "INC0010001"
    assert incident.caller == "Amira Haddad"
    assert incident.assignment_group == "AI Support Agent"
    url = session.get.call_args.args[0]
    params = session.get.call_args.kwargs["params"]
    assert url.endswith("/api/now/table/incident")
    assert params["sysparm_query"] == "number=INC0010001"
    assert params["sysparm_display_value"] == "true"
    assert session.get.call_args.kwargs["timeout"] == 30.0


def test_get_by_sys_id_uses_record_path(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(200, {"result": INCIDENT_RECORD})

    incident = client.get_incident(SYS_ID.upper())

    assert incident.sys_id == SYS_ID
    assert session.get.call_args.args[0].endswith(f"/api/now/table/incident/{SYS_ID}")
    assert "sysparm_query" not in session.get.call_args.kwargs["params"]


def test_number_not_found_raises(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(200, {"result": []})
    with pytest.raises(IncidentNotFoundError, match="INC9999999"):
        client.get_incident("INC9999999")


def test_sys_id_not_found_raises(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(404, {"error": {"message": "No Record found"}})
    with pytest.raises(IncidentNotFoundError):
        client.get_incident(SYS_ID)


@pytest.mark.parametrize("bad", ["", "hello", "INC001^ORactive=true", "12345", "INC12"])
def test_invalid_identifier_rejected_without_request(
    client: ServiceNowClient, session: MagicMock, bad: str
) -> None:
    with pytest.raises(InvalidIdentifierError):
        client.get_incident(bad)
    session.get.assert_not_called()


@pytest.mark.parametrize(
    ("status", "error_cls"),
    [(401, ServiceNowAuthError), (403, ServiceNowPermissionError), (500, ServiceNowError)],
)
def test_http_errors_map_to_typed_exceptions(
    client: ServiceNowClient, session: MagicMock, status: int, error_cls: type[Exception]
) -> None:
    body = {"error": {"message": "User is not authenticated", "detail": "Required auth"}}
    session.get.return_value = make_response(status, body)
    with pytest.raises(error_cls, match=f"HTTP {status}"):
        client.get_incident("INC0010001")


def test_network_error_is_wrapped(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.side_effect = requests.ConnectionError("boom")
    with pytest.raises(ServiceNowError, match="Could not reach ServiceNow: ConnectionError"):
        client.get_incident("INC0010001")


def test_non_json_response(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(200, ValueError("not json"))
    with pytest.raises(ServiceNowError, match="non-JSON"):
        client.get_incident("INC0010001")


def test_errors_never_contain_password(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(401, {"error": {"message": "nope"}})
    with pytest.raises(ServiceNowAuthError) as excinfo:
        client.get_incident("INC0010001")
    assert "super-secret-test-password" not in str(excinfo.value)


def test_settings_repr_hides_password(client: ServiceNowClient) -> None:
    assert "super-secret-test-password" not in repr(client._settings)


def test_client_is_read_only(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(200, {"result": [INCIDENT_RECORD]})
    client.get_incident("INC0010001")
    for method in ("post", "put", "patch", "delete", "request"):
        getattr(session, method).assert_not_called()
    public = {name for name in dir(client) if not name.startswith("_")}
    assert public == {"get_incident"}
