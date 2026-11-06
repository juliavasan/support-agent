"""Shared fixtures: mocked ServiceNow HTTP responses. No test touches the network."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from support_agent.analysis.models import TriageAnalysis, TriageAssessment
from support_agent.config import AgentSettings, ServiceNowSettings
from support_agent.retrieval.models import RetrievalResult, RetrievedDocument
from support_agent.servicenow import Incident, ServiceNowClient, ServiceNowError, WorkNote

SYS_ID = "7b17848cc3d05310783ef9477d013199"

INCIDENT_RECORD: dict[str, Any] = {
    "sys_id": SYS_ID,
    "number": "INC0010001",
    "short_description": (
        "Some ERPNext screens stay in English after switching my language to Arabic"
    ),
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


# --- Phase 2: LLM analysis and work notes (all fakes, no network) ---------------------

ANALYSIS = TriageAnalysis(
    summary="UI labels stay in English after the user switches language to Arabic.",
    impact="Low",
    impact_reason="One user, cosmetic issue with a workaround.",
    urgency="Low",
    urgency_reason="No deadline or blocked process mentioned.",
    category="Configuration",
    potential_causes=["Translations for some doctypes may be missing for Arabic"],
    troubleshooting_steps=[
        "Reproduce with a test user set to Arabic",
        "Check the translation table",
    ],
    missing_information=["Which screens or doctypes show English labels"],
    relevant_documents=[],
    possible_match_id="",
    possible_match_reason="",
)


def make_assessment(**overrides: Any) -> TriageAssessment:
    values: dict[str, Any] = {
        "analysis": ANALYSIS,
        "priority": 5,
        "priority_label": "P5 - Planning",
        "model": "claude-opus-5-5",
        "input_tokens": 900,
        "output_tokens": 400,
    }
    values.update(overrides)
    return TriageAssessment(**values)


class FakeAnalyzer:
    def __init__(self, assessment: TriageAssessment | None = None, error: Exception | None = None):
        self.assessment = assessment or make_assessment()
        self.error = error
        self.calls: list[Incident] = []
        self.contexts: list[RetrievalResult | None] = []

    def analyze(
        self, incident: Incident, context: RetrievalResult | None = None
    ) -> TriageAssessment:
        self.calls.append(incident)
        self.contexts.append(context)
        if self.error:
            raise self.error
        return self.assessment


def retrieved(doc_id: str, kind: str = "kb_article", **overrides: Any) -> RetrievedDocument:
    values: dict[str, Any] = {
        "id": doc_id,
        "kind": kind,
        "title": f"Title of {doc_id}",
        "snippet": f"Snippet of {doc_id}",
        "relevance": 8,
        "reason": "Same feature and symptom.",
    }
    values.update(overrides)
    return RetrievedDocument(**values)


def make_context(**overrides: Any) -> RetrievalResult:
    values: dict[str, Any] = {
        "kb_articles": [retrieved("KB0010005")],
        "known_issues": [retrieved("KB0010074", "known_issue", reference="frappe/erpnext#1")],
        "similar_incidents": [retrieved("INC0010006", "incident", state="New")],
        "candidates_considered": 12,
        "threshold": 7,
        "rerank_model": "claude-haiku-5-5",
    }
    values.update(overrides)
    return RetrievalResult(**values)


class FakeRetriever:
    def __init__(self, result: RetrievalResult | None = None, error: Exception | None = None):
        self.result = result if result is not None else RetrievalResult(threshold=7)
        self.error = error
        self.calls: list[Incident] = []

    def retrieve(self, incident: Incident) -> RetrievalResult:
        self.calls.append(incident)
        if self.error:
            raise self.error
        return self.result


class FakeServiceNow:
    """In-memory stand-in for ServiceNowClient. Records every write."""

    def __init__(self, record: dict[str, Any] | None = None):
        self.record = dict(record or INCIDENT_RECORD)
        self.notes: list[WorkNote] = []
        self.writes: list[tuple[str, str]] = []
        self.fail_on: set[str] = set()  # method names that raise ServiceNowError
        self.drop_writes = False  # simulate a write that never reaches the journal
        self.change_on_write: dict[str, str] = {}  # simulate a business rule editing fields

    def _maybe_fail(self, method: str) -> None:
        if method in self.fail_on:
            raise ServiceNowError(f"{method} failed (simulated)")

    def get_incident(self, identifier: str) -> Incident:
        self._maybe_fail("get_incident")
        return Incident.from_api(self.record)

    def list_work_notes(self, incident_sys_id: str) -> list[WorkNote]:
        self._maybe_fail("list_work_notes")
        return list(reversed(self.notes))

    def add_work_note(self, incident_sys_id: str, text: str) -> None:
        self._maybe_fail("add_work_note")
        self.writes.append((incident_sys_id, text))
        if not self.drop_writes:
            self.notes.append(
                WorkNote(
                    value=text,
                    created_on="2026-10-27 15:00:00",
                    created_by="Support Agent",
                )
            )
        self.record.update(self.change_on_write)


def write_settings(**overrides: Any) -> AgentSettings:
    values: dict[str, Any] = {"write_test_incidents": frozenset({"INC0010001"})}
    values.update(overrides)
    return AgentSettings(**values)
