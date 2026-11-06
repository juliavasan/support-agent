from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from conftest import INCIDENT_RECORD, SYS_ID, make_response
from support_agent.servicenow import (
    Incident,
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


def test_get_incident_only_issues_get(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(200, {"result": [INCIDENT_RECORD]})
    client.get_incident("INC0010001")
    for method in ("post", "put", "patch", "delete", "request"):
        getattr(session, method).assert_not_called()


def test_public_api_is_limited(client: ServiceNowClient) -> None:
    public = {name for name in dir(client) if not name.startswith("_")}
    # Reads may grow (the search index needs incident listings); the only write is
    # add_work_note.
    assert public == {
        "get_incident",
        "list_work_notes",
        "add_work_note",
        "list_incident_versions",
        "get_index_incidents",
    }


JOURNAL = (
    "2026-11-05 16:33:09 - Support Agent (Work notes)\n"
    "AI Triage Assessment (Preliminary)\n\nSummary: second\n"
    "[support-agent:triage v1 run=bbb]\n\n"
    "2026-10-27 18:46:09 - Fred Luddy (Work notes)\n"
    "Called the user.\n\n"
)


def test_list_work_notes_reads_the_incident_field(
    client: ServiceNowClient, session: MagicMock
) -> None:
    # Not sys_journal_field: its row ACL returns 200 with no rows for the agent account.
    session.get.return_value = make_response(200, {"result": {"work_notes": JOURNAL}})

    notes = client.list_work_notes(SYS_ID)

    assert session.get.call_args.args[0].endswith(f"/api/now/table/incident/{SYS_ID}")
    params = session.get.call_args.kwargs["params"]
    assert (params["sysparm_fields"], params["sysparm_display_value"]) == ("work_notes", "true")
    assert [(n.created_on, n.created_by) for n in notes] == [
        ("2026-11-05 16:33:09", "Support Agent"),
        ("2026-10-27 18:46:09", "Fred Luddy"),
    ]
    assert notes[0].value.startswith("AI Triage Assessment")
    assert notes[0].value.endswith("run=bbb]")
    assert notes[1].value == "Called the user."


def test_no_work_notes_is_an_empty_list(client: ServiceNowClient, session: MagicMock) -> None:
    session.get.return_value = make_response(200, {"result": {"work_notes": ""}})
    assert client.list_work_notes(SYS_ID) == []


def test_add_work_note_writes_only_work_notes(client: ServiceNowClient, session: MagicMock) -> None:
    session.patch.return_value = make_response(200, {"result": {"sys_id": SYS_ID}})

    client.add_work_note(SYS_ID, "AI Triage Assessment (Preliminary)\n...")

    url = session.patch.call_args.args[0]
    assert url.endswith(f"/api/now/table/incident/{SYS_ID}")
    assert session.patch.call_args.kwargs["json"] == {
        "work_notes": "AI Triage Assessment (Preliminary)\n..."
    }
    assert session.patch.call_args.kwargs["timeout"] == 30.0
    for method in ("post", "put", "delete"):
        getattr(session, method).assert_not_called()


@pytest.mark.parametrize("bad_sys_id", ["INC0010001", "", "x" * 32, f"{SYS_ID}^ORactive=true"])
def test_write_and_list_require_a_sys_id(
    client: ServiceNowClient, session: MagicMock, bad_sys_id: str
) -> None:
    with pytest.raises(InvalidIdentifierError):
        client.add_work_note(bad_sys_id, "note")
    with pytest.raises(InvalidIdentifierError):
        client.list_work_notes(bad_sys_id)
    session.patch.assert_not_called()
    session.get.assert_not_called()


@pytest.mark.parametrize("text", ["", "   ", "x" * 4001])
def test_empty_or_oversized_notes_are_refused_without_a_request(
    client: ServiceNowClient, session: MagicMock, text: str
) -> None:
    with pytest.raises(ServiceNowError):
        client.add_work_note(SYS_ID, text)
    session.patch.assert_not_called()


def test_write_permission_error_is_typed(client: ServiceNowClient, session: MagicMock) -> None:
    session.patch.return_value = make_response(403, {"error": {"message": "ACL"}})
    with pytest.raises(ServiceNowPermissionError, match="HTTP 403"):
        client.add_work_note(SYS_ID, "note")


def test_company_falls_back_to_callers_company() -> None:
    record = {**INCIDENT_RECORD, "company": "", "caller_id.company": "Cobalt Ridge Manufacturing"}
    assert Incident.from_api(record).company == "Cobalt Ridge Manufacturing"


def test_incident_company_wins_over_callers_company() -> None:
    record = {
        **INCIDENT_RECORD,
        "company": "Harborview Foods",
        "caller_id.company": "Cobalt Ridge Manufacturing",
    }
    assert Incident.from_api(record).company == "Harborview Foods"


# --- search-index reads ---------------------------------------------------------------


def test_listing_pages_until_a_short_page(client: ServiceNowClient, session: MagicMock) -> None:
    full = [{"sys_id": f"{i:032x}", "sys_updated_on": "t"} for i in range(500)]
    session.get.side_effect = [
        make_response(200, {"result": full}),
        make_response(200, {"result": full[:3]}),
    ]

    versions = client.list_incident_versions()

    assert len(versions) == 503
    offsets = [c.kwargs["params"]["sysparm_offset"] for c in session.get.call_args_list]
    assert offsets == ["0", "500"]


def test_fetch_by_sys_ids_batches_and_validates(
    client: ServiceNowClient, session: MagicMock
) -> None:
    session.get.return_value = make_response(200, {"result": []})
    ids = [f"{i:032x}" for i in range(120)]

    client.get_index_incidents(ids)

    queries = [c.kwargs["params"]["sysparm_query"] for c in session.get.call_args_list]
    assert len(queries) == 3  # 50 + 50 + 20
    assert queries[0].startswith(f"sys_idIN{ids[0]},")
    with pytest.raises(InvalidIdentifierError):
        client.get_index_incidents(["not-a-sys-id^ORactive=true"])
