"""ServiceNow Table API client.

Reads incidents, work-note journal entries, and (for the search index) incident
summaries. The only write is `add_work_note`,
which can change nothing but the internal `work_notes` journal: the request body is
built here, never passed in, so a caller cannot widen it to other fields.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from support_agent.config import ServiceNowSettings
from support_agent.servicenow.models import (
    INCIDENT_FIELDS,
    INDEX_INCIDENT_FIELDS,
    Incident,
    IndexIncident,
    RecordVersion,
    WorkNote,
    parse_work_notes,
)

INCIDENT_NUMBER_RE = re.compile(r"^INC\d{7,}$", re.IGNORECASE)
SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)

# ServiceNow journal entries are capped at 4,000 characters by default.
MAX_WORK_NOTE_CHARS = 4000
PAGE_SIZE = 500
BATCH_SIZE = 50  # sys_ids per "sys_idIN..." query, keeps URLs short


class ServiceNowError(RuntimeError):
    """Base error for ServiceNow API failures. Messages never contain credentials."""


class InvalidIdentifierError(ServiceNowError):
    pass


class IncidentNotFoundError(ServiceNowError):
    pass


class ServiceNowAuthError(ServiceNowError):
    pass


class ServiceNowPermissionError(ServiceNowError):
    pass


def _api_error_message(response: requests.Response) -> str:
    """Extract ServiceNow's error message only, never request headers or auth."""
    try:
        error = response.json().get("error", {})
        message = f"{error.get('message', '')} - {error.get('detail', '')}".strip(" -")
    except (ValueError, AttributeError):
        message = ""
    return message or response.reason or "no details"


def _require_sys_id(sys_id: str) -> str:
    if not SYS_ID_RE.match(sys_id):
        raise InvalidIdentifierError(f"'{sys_id}' is not a 32-character sys_id")
    return sys_id.lower()


class ServiceNowClient:
    def __init__(self, settings: ServiceNowSettings, session: requests.Session | None = None):
        self._settings = settings
        self._session = session or requests.Session()
        self._session.auth = (settings.username, settings.password)
        self._session.headers.update({"Accept": "application/json"})

    def get_incident(self, identifier: str) -> Incident:
        """Fetch one incident by number (INC0010001) or 32-character sys_id."""
        identifier = identifier.strip()
        params: dict[str, str] = {
            "sysparm_fields": ",".join(INCIDENT_FIELDS),
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
        }
        if SYS_ID_RE.match(identifier):
            path = f"incident/{identifier.lower()}"
        elif INCIDENT_NUMBER_RE.match(identifier):
            # The strict regex above also prevents encoded-query injection (e.g. "^OR...").
            path = "incident"
            params.update({"sysparm_query": f"number={identifier.upper()}", "sysparm_limit": "1"})
        else:
            raise InvalidIdentifierError(
                f"'{identifier}' is not an incident number (e.g. INC0010001) "
                "or a 32-character sys_id"
            )

        result = self._get(path, params, identifier)
        record = result[0] if isinstance(result, list) and result else result
        if not record:
            raise IncidentNotFoundError(f"Incident {identifier} not found")
        return Incident.from_api(record)

    def list_work_notes(self, incident_sys_id: str) -> list[WorkNote]:
        """Return the incident's work notes, newest first.

        Read through the incident's `work_notes` field (display value), not the
        sys_journal_field table: a row-level ACL on that table returns 200 with no rows
        to accounts without journal-table access (support_agent among them), which made
        the duplicate check and write verification blind. Reading the field applies the
        same rule a person sees on the incident form.
        """
        sys_id = _require_sys_id(incident_sys_id)
        params = {"sysparm_fields": "work_notes", "sysparm_display_value": "true"}
        record = self._get(f"incident/{sys_id}", params, sys_id)
        journal = record.get("work_notes", "") if isinstance(record, dict) else ""
        return parse_work_notes(str(journal))

    def list_incident_versions(self) -> list[RecordVersion]:
        """sys_id + updated time of every incident the agent can read."""
        records = self._get_all("incident", "", ("sys_id", "sys_updated_on"))
        return [RecordVersion.from_api(record) for record in records]

    def get_index_incidents(self, sys_ids: list[str]) -> list[IndexIncident]:
        records = self._get_by_sys_ids("incident", sys_ids, INDEX_INCIDENT_FIELDS)
        return [IndexIncident.from_api(record) for record in records]

    def add_work_note(self, incident_sys_id: str, text: str) -> None:
        """Append an internal work note. Never touches customer-visible comments or any
        other field. ServiceNow appends journal entries, so existing notes are kept."""
        sys_id = _require_sys_id(incident_sys_id)
        if not text.strip():
            raise ServiceNowError("Refusing to write an empty work note")
        if len(text) > MAX_WORK_NOTE_CHARS:
            raise ServiceNowError(
                f"Work note is {len(text)} characters (limit {MAX_WORK_NOTE_CHARS})"
            )
        body = {"work_notes": text}  # the only field this client ever writes
        self._send(
            "PATCH", f"incident/{sys_id}", {"sysparm_fields": "sys_id"}, sys_id, json_body=body
        )

    def _get(self, path: str, params: dict[str, str], identifier: str) -> Any:
        return self._send("GET", path, params, identifier)

    def _get_all(self, table: str, query: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        """Page through a table query. ORDERBYsys_id keeps pages stable."""
        records: list[dict[str, Any]] = []
        while True:
            params = {
                "sysparm_query": f"{query}^ORDERBYsys_id" if query else "ORDERBYsys_id",
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": str(PAGE_SIZE),
                "sysparm_offset": str(len(records)),
            }
            page = self._get(table, params, table) or []
            records.extend(page)
            if len(page) < PAGE_SIZE:
                return records

    def _get_by_sys_ids(
        self, table: str, sys_ids: list[str], fields: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        valid = [_require_sys_id(sys_id) for sys_id in sys_ids]
        records: list[dict[str, Any]] = []
        for start in range(0, len(valid), BATCH_SIZE):
            batch = ",".join(valid[start : start + BATCH_SIZE])
            records.extend(self._get_all(table, f"sys_idIN{batch}", fields))
        return records

    def _send(
        self,
        method: str,
        path: str,
        params: dict[str, str],
        identifier: str,
        json_body: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self._settings.instance_url}/api/now/table/{path}"
        timeout = self._settings.timeout_seconds
        try:
            if method == "GET":
                response = self._session.get(url, params=params, timeout=timeout)
            else:
                response = self._session.patch(url, params=params, json=json_body, timeout=timeout)
        except requests.RequestException as exc:
            raise ServiceNowError(f"Could not reach ServiceNow: {type(exc).__name__}") from None

        if response.status_code == 404:
            raise IncidentNotFoundError(f"Incident {identifier} not found")
        if response.status_code == 401:
            raise ServiceNowAuthError(
                f"Authentication failed (HTTP 401): {_api_error_message(response)}"
            )
        if response.status_code == 403:
            raise ServiceNowPermissionError(
                f"Permission denied (HTTP 403): {_api_error_message(response)}"
            )
        if response.status_code != 200:
            raise ServiceNowError(
                f"ServiceNow returned HTTP {response.status_code}: {_api_error_message(response)}"
            )
        try:
            return response.json().get("result")
        except ValueError:
            raise ServiceNowError("ServiceNow returned a non-JSON response") from None
