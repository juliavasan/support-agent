"""Graph nodes. Each node takes the state and returns a partial state update."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import psycopg

from support_agent.analysis.analyzer import AnalysisError
from support_agent.analysis.customer_tiers import (
    CustomerTierError,
    CustomerTiers,
    apply_customer_tier,
)
from support_agent.analysis.models import TriageAssessment
from support_agent.analysis.priority import PriorityMatrixError, priority_label
from support_agent.analysis.work_note import format_work_note, is_triage_note, marker
from support_agent.config import AgentSettings, ConfigError
from support_agent.graph.state import IncidentState
from support_agent.guardrails.write_policy import check_work_note_write
from support_agent.retrieval.embeddings import EmbeddingError
from support_agent.retrieval.models import RetrievalResult, RetrievedDocument
from support_agent.retrieval.pg_index import IndexConfigError
from support_agent.retrieval.reranker import RetrievalError
from support_agent.servicenow import Incident, ServiceNowClient, ServiceNowError


class Analyzer(Protocol):
    def analyze(
        self, incident: Incident, context: RetrievalResult | None = None
    ) -> TriageAssessment: ...


class ContextRetriever(Protocol):
    def retrieve(self, incident: Incident) -> RetrievalResult: ...


class Node(Protocol):
    """A graph node. LangGraph's node protocol requires the parameter to be named `state`."""

    def __call__(self, state: IncidentState) -> dict[str, Any]: ...


ClientFactory = Callable[[], ServiceNowClient]
AnalyzerFactory = Callable[[], Analyzer]
SettingsFactory = Callable[[], AgentSettings]
TiersFactory = Callable[[], CustomerTiers]
RetrieverFactory = Callable[[], ContextRetriever]

DISPLAY_FIELDS = (
    ("Number", "number"),
    ("Short description", "short_description"),
    ("Priority", "priority"),
    ("Impact", "impact"),
    ("Urgency", "urgency"),
    ("State", "state"),
    ("Category", "category"),
    ("Assignment group", "assignment_group"),
    ("Assigned to", "assigned_to"),
    ("Caller", "caller"),
    ("Company", "company"),
    ("Opened", "opened_at"),
    ("sys_id", "sys_id"),
)


def _append(state: IncidentState, section: str) -> str:
    current = state.get("display") or ""
    return f"{current}\n\n{section}" if current else section


def make_retrieve_incident(
    client_factory: ClientFactory,
) -> Node:
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


def _context(state: IncidentState) -> RetrievalResult | None:
    raw = state.get("context")
    return RetrievalResult.model_validate(raw) if raw else None


def _found(doc: RetrievedDocument) -> str:
    matched = f", {'+'.join(doc.matched_by)}" if doc.matched_by else ""
    return f"{doc.id} ({doc.relevance}/10{matched})"


def _retrieval_summary(result: RetrievalResult) -> str:
    lines = [
        f"Retrieval: {result.candidates_considered} candidates"
        + (f" from {result.search_mode} search" if result.search_mode else "")
        + f", threshold {result.threshold}/10"
        + (
            f", reranked by {result.rerank_model} "
            f"({result.input_tokens} in / {result.output_tokens} out tokens)"
            if result.rerank_model
            else ""
        )
    ]
    groups = (
        ("KB articles", result.kb_articles),
        ("Known issues", result.known_issues),
        ("Similar incidents", result.similar_incidents),
    )
    for label, docs in groups:
        found = ", ".join(_found(d) for d in docs) or "none"
        lines.append(f"  {label}: {found}")
    lines += [f"  WARNING: {warning}" for warning in result.warnings]
    return "\n".join(lines)


def make_retrieve_context(retriever_factory: RetrieverFactory) -> Node:
    """Build the retrieval node. Retrieval improves triage but isn't required for it:
    on failure the run continues without context and the note says so."""

    def retrieve_context(state: IncidentState) -> dict[str, Any]:
        if state.get("error") or not state.get("incident"):
            return {"context": None}
        incident = Incident.model_validate(state["incident"])
        try:
            result = retriever_factory().retrieve(incident)
        except (
            RetrievalError,
            EmbeddingError,
            IndexConfigError,
            ServiceNowError,
            ConfigError,
            sqlite3.Error,
            psycopg.Error,
            OSError,
        ) as exc:
            message = f"Retrieval skipped ({type(exc).__name__}): {exc}"
            return {"context": None, "display": _append(state, f"WARNING: {message}")}
        return {
            "context": result.model_dump(),
            "display": _append(state, _retrieval_summary(result)),
        }

    return retrieve_context


def make_analyze_incident(
    analyzer_factory: AnalyzerFactory,
) -> Node:
    """Build the LLM analysis node. Failures are written to state; the graph never crashes."""

    def analyze_incident(state: IncidentState) -> dict[str, Any]:
        if state.get("error") or not state.get("incident"):
            return {"assessment": None}
        try:
            incident = Incident.model_validate(state["incident"])
            assessment = analyzer_factory().analyze(incident, _context(state))
        except (AnalysisError, PriorityMatrixError, ConfigError) as exc:
            message = f"LLM analysis failed ({type(exc).__name__}): {exc}"
            return {
                "assessment": None,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "display": _append(state, f"ERROR: {message}"),
            }
        usage = f"{assessment.input_tokens} in / {assessment.output_tokens} out tokens"
        section = (
            f"LLM analysis: {assessment.model} ({usage})\n"
            f"Recommended priority: {assessment.priority_label} "
            f"(impact {assessment.analysis.impact} x urgency {assessment.analysis.urgency})"
        )
        cited = ", ".join(assessment.analysis.relevant_documents) or "none"
        section += f"\nCited documents: {cited}"
        if assessment.analysis.possible_match_id:
            section += f"\nPossible match: {assessment.analysis.possible_match_id}"
        if assessment.dropped_references:
            dropped = ", ".join(assessment.dropped_references)
            section += f"\nWARNING: dropped citations not in the retrieved set: {dropped}"
        return {"assessment": assessment.model_dump(), "display": _append(state, section)}

    return analyze_incident


def make_apply_customer_policy(tiers_factory: TiersFactory) -> Node:
    """Build the customer-tier node: a deterministic rule applied after the LLM.

    An unreadable tier config stops the run (no note is written) rather than silently
    treating a premium customer as standard.
    """

    def apply_customer_policy(state: IncidentState) -> dict[str, Any]:
        if state.get("error") or not state.get("assessment") or not state.get("incident"):
            return {}
        incident = Incident.model_validate(state["incident"])
        assessment = TriageAssessment.model_validate(state["assessment"])
        try:
            adjustment = apply_customer_tier(assessment.priority, incident.company, tiers_factory())
        except (CustomerTierError, ConfigError) as exc:
            return {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "display": _append(state, f"ERROR: Customer policy failed: {exc}"),
            }
        updated = assessment.model_copy(
            update={
                "priority": adjustment.priority,
                "priority_label": priority_label(adjustment.priority),
                "customer_adjustment": adjustment,
            }
        )
        section = (
            f"Customer policy: {adjustment.customer or '(no company)'} -> {adjustment.tier} tier\n"
            f"Recommended priority: {updated.priority_label} ({adjustment.rule})"
        )
        return {"assessment": updated.model_dump(), "display": _append(state, section)}

    return apply_customer_policy


class _Outcome:
    """Builds the write node's state update. Keeps every exit path consistent."""

    def __init__(self, state: IncidentState, mode: str, note: str | None = None):
        self.mode = mode
        self.note = note
        self.display = state.get("display") or ""
        if note:
            self.display += f"\n\nProposed work note ({len(note)} chars):\n{note}"

    def __call__(
        self,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
        error: bool = False,
    ) -> dict[str, Any]:
        update: dict[str, Any] = {
            "work_note": self.note,
            "write_result": {
                "status": status,
                "mode": self.mode,
                "message": message,
                **(details or {}),
            },
            "display": f"{self.display}\n\nWrite result: {status.upper()}: {message}",
        }
        if error:
            update["error"] = message
        return update


def make_write_work_note(client_factory: ClientFactory, settings_factory: SettingsFactory) -> Node:
    """Build the work-note node: dry-run by default, guarded and verified in write mode."""

    def write_work_note(state: IncidentState) -> dict[str, Any]:
        requested = state.get("mode")
        if state.get("error") or not state.get("assessment") or not state.get("incident"):
            return _Outcome(state, requested or "dry_run")("skipped", "No analysis to write")
        try:
            settings = settings_factory()
        except ConfigError as exc:
            return _Outcome(state, requested or "dry_run")("failed", str(exc), error=True)
        mode = requested or settings.default_run_mode

        incident = Incident.model_validate(state["incident"])
        assessment = TriageAssessment.model_validate(state["assessment"])
        run_id = uuid.uuid4().hex[:12]
        try:
            note = format_work_note(assessment, run_id, _context(state))
        except ValueError as exc:
            return _Outcome(state, mode)("failed", str(exc), error=True)
        outcome = _Outcome(state, mode, note)
        client = client_factory()

        # Read-only lookup used in both modes: has this agent already triaged the incident?
        try:
            notes = client.list_work_notes(incident.sys_id)
        except ServiceNowError as exc:
            if outcome.mode != "write":
                return outcome("dry_run", f"Not written (dry run). Duplicate check failed: {exc}")
            return outcome("failed", f"Could not read existing work notes: {exc}", error=True)
        existing = next((n for n in notes if is_triage_note(n.value)), None)
        duplicate: dict[str, Any] = (
            {"existing_note_created_on": existing.created_on} if existing else {}
        )

        if outcome.mode != "write":
            message = "Not written (dry run)."
            if existing:
                message += (
                    f" A triage note already exists ({existing.created_on}); write mode would skip."
                )
            return outcome("dry_run", message, duplicate)

        decision = check_work_note_write(incident.number, settings)
        if not decision.allowed:
            return outcome("blocked", decision.reason, error=True)
        if existing:
            message = f"A triage note already exists ({existing.created_on}); nothing written."
            return outcome("skipped_duplicate", message, duplicate)

        try:
            before = client.get_incident(incident.sys_id).protected_values()
            client.add_work_note(incident.sys_id, note)
            entry = next(
                (n for n in client.list_work_notes(incident.sys_id) if marker(run_id) in n.value),
                None,
            )
            after = client.get_incident(incident.sys_id).protected_values()
        except ServiceNowError as exc:
            return outcome("failed", f"ServiceNow error during write: {exc}", error=True)

        changed = sorted(name for name in before if before[name] != after[name])
        details: dict[str, Any] = {
            "run_id": run_id,
            "verified": entry is not None and not changed,
            "journal_created_on": entry.created_on if entry else None,
            "journal_created_by": entry.created_by if entry else None,
            "changed_protected_fields": changed,
        }
        if entry is None:
            message = "Write sent, but the note was not found in the incident's work notes"
            return outcome("unverified", message, details, error=True)
        if changed:
            message = f"Note written, but protected fields changed: {', '.join(changed)}"
            return outcome("unverified", message, details, error=True)
        message = (
            f"Work note written and verified on the incident ({entry.created_on} by "
            f"{entry.created_by}). Protected fields unchanged."
        )
        return outcome("written", message, details)

    return write_work_note
