"""Read-only ServiceNow Table API client.

Phase 1 is read-only by construction: this client only issues GET requests.
Write operations will be added later behind the guardrails policy layer.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from support_agent.config import ServiceNowSettings
from support_agent.servicenow.models import INCIDENT_FIELDS, Incident

INCIDENT_NUMBER_RE = re.compile(r"^INC\d{7,}$", re.IGNORECASE)
SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


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
                f"'{identifier}' is not an incident number (e.g. INC0010001) or a 32-character sys_id"
            )

        result = self._get(path, params, identifier)
        record = result[0] if isinstance(result, list) and result else result
        if not record:
            raise IncidentNotFoundError(f"Incident {identifier} not found")
        return Incident.from_api(record)

    def _get(self, path: str, params: dict[str, str], identifier: str) -> Any:
        url = f"{self._settings.instance_url}/api/now/table/{path}"
        try:
            response = self._session.get(url, params=params, timeout=self._settings.timeout_seconds)
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
