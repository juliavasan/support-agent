"""Shared fixtures: mocked ServiceNow HTTP responses. No test touches the network."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from support_agent.config import ServiceNowSettings
from support_agent.servicenow import ServiceNowClient

SYS_ID = "7b17848cc3d05310783ef9477d013199"

INCIDENT_RECORD: dict[str, Any] = {
    "sys_id": SYS_ID,
    "number": "INC0010001",
    "short_description": "Some ERPNext screens stay in English after switching my language to Arabic",
    "description": "Hi,\nSome labels are still in English.\nThanks,\nAmira",
    "priority": "4 - Low",
    "state": "New",
    "category": "Software",
    "assignment_group": "AI Support Agent",
    "assigned_to": "",
    "caller_id": "Amira Haddad",
    "opened_at": "2026-10-27 13:10:15",
}


def make_response(status: int, body: Any = None, reason: str = "") -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.reason = reason
    if isinstance(body, Exception):
        response.json.side_effect = body
    else:
        response.json.return_value = body
    return response


@pytest.fixture
def settings() -> ServiceNowSettings:
    return ServiceNowSettings(
        instance_url="https://dev000000.service-now.com",
        username="support_agent",
        password="super-secret-test-password",
    )


@pytest.fixture
def session() -> MagicMock:
    mock = MagicMock(spec=requests.Session)
    mock.headers = {}
    return mock


@pytest.fixture
def client(settings: ServiceNowSettings, session: MagicMock) -> ServiceNowClient:
    return ServiceNowClient(settings, session=session)
