"""Analyze an incident with Claude using structured output."""

from __future__ import annotations

from typing import Any

import anthropic

from support_agent.analysis.models import TriageAnalysis, TriageAssessment
from support_agent.analysis.priority import (
    PriorityMatrix,
    priority_label,
    recommend_priority,
)
from support_agent.config import AgentSettings
from support_agent.llm import LLMError, default_llm
from support_agent.prompts import load_prompt
from support_agent.retrieval.models import RetrievalResult
from support_agent.servicenow.models import Incident

MAX_TOKENS = 16000
# Server-side refusal fallback: if the model declines (e.g. a safety-classifier false
# positive), the API retries on a suitable model within the same request. Only some
# models support it; Claude Haiku 5.5 must not be sent the parameter.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)

# Sent to the model. Caller identity and current priority/impact/urgency are left out:
# the caller's name isn't needed for diagnosis, and the current values would anchor
# the model's own estimate.
ANALYSIS_FIELDS = (
    ("Number", "number"),
    ("Short description", "short_description"),
    ("Category (as filed)", "category"),
    ("State", "state"),
    ("Assignment group", "assignment_group"),
    ("Opened", "opened_at"),
    ("Description", "description"),
)


class AnalysisError(RuntimeError):
    """The LLM analysis could not be completed. Messages never contain the API key."""


WRAPPER_TAGS = ("incident", "retrieved_documents", "document")


def _neutralize(text: str) -> str:
    # Stops ticket or document text from closing our data wrappers and posing as
    # instructions.
    for tag in WRAPPER_TAGS:
        text = text.replace(f"<{tag}", f"&lt;{tag}").replace(f"</{tag}", f"&lt;/{tag}")
    return text


def _documents_block(context: RetrievalResult) -> str:
    docs = context.documents()
    if not docs:
        return "<retrieved_documents>\nNo relevant documents were found.\n</retrieved_documents>"
    parts = []
    for doc in docs:
        details = [f"Title: {_neutralize(doc.title)}"]
        if doc.kind == "incident":
            details.append(f"State: {_neutralize(doc.state) or '-'}")
            if doc.resolution:
                details.append(f"Resolution notes: {_neutralize(doc.resolution[:500])}")
        if doc.reference:
            details.append(f"Upstream: {doc.reference} (open, not fixed)")
        if doc.heading:
            details.append(f"Section: {_neutralize(doc.heading)}")
        details.append(_neutralize(doc.snippet))
        parts.append(
            f'<document id="{doc.id}" type="{doc.kind}">\n' + "\n".join(details) + "\n</document>"
        )
    return "<retrieved_documents>\n" + "\n".join(parts) + "\n</retrieved_documents>"


def build_incident_message(incident: Incident, context: RetrievalResult | None = None) -> str:
    lines = []
    for label, key in ANALYSIS_FIELDS:
        value = (getattr(incident, key) or "-").strip()
        lines.append(f"{label}: {_neutralize(value)}")
    body = "\n".join(lines)
    message = f"Triage the incident below.\n\n<incident>\n{body}\n</incident>"
    if context is not None:
        message += "\n\n" + _documents_block(context)
    return message


def _check_references(
    analysis: TriageAnalysis, context: RetrievalResult | None
) -> tuple[TriageAnalysis, list[str]]:
    """Drop document IDs the model cited that we did not retrieve."""
    allowed = context.ids() if context else set()
    matchable = {
        doc.id for doc in (context.known_issues + context.similar_incidents if context else [])
    }
    kept = [ref for ref in dict.fromkeys(analysis.relevant_documents) if ref in allowed]
    dropped = [ref for ref in analysis.relevant_documents if ref not in allowed]
    update: dict[str, object] = {"relevant_documents": kept}
    match_id = analysis.possible_match_id.strip()
    if match_id and match_id not in matchable:
        dropped.append(match_id)
        update.update(possible_match_id="", possible_match_reason="")
    return analysis.model_copy(update=update), dropped


class IncidentAnalyzer:
    def __init__(
        self,
        settings: AgentSettings,
        matrix: PriorityMatrix,
        client: anthropic.Anthropic | None = None,
    ):
        self._settings = settings
        self._matrix = matrix
        self._llm = default_llm(settings, client)
        self._system = load_prompt("triage_system")

    def analyze(
        self, incident: Incident, context: RetrievalResult | None = None
    ) -> TriageAssessment:
        fallback: dict[str, Any] = {}
        if self._settings.triage_model in FALLBACK_MODELS:
            fallback = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        try:
            response = self._llm.parse(
                model=self._settings.triage_model,
                system=self._system,
                user=build_incident_message(incident, context),
                output_format=TriageAnalysis,
                max_tokens=MAX_TOKENS,
                effort=self._settings.triage_effort,
                api_options=fallback,
            )
        except LLMError as exc:
            raise AnalysisError(str(exc)) from None

        if response.stop_reason == "refusal":
            category = response.refusal_category
            raise AnalysisError(f"The model declined to analyze this incident ({category})")
        if response.stop_reason == "max_tokens":
            raise AnalysisError("The model's output was cut off (max_tokens reached)")
        analysis = response.output
        if analysis is None:
            raise AnalysisError("The model returned no structured output")

        analysis, dropped = _check_references(analysis, context)
        priority = recommend_priority(analysis.impact, analysis.urgency, self._matrix)
        return TriageAssessment(
            analysis=analysis,
            dropped_references=dropped,
            priority=priority,
            priority_label=priority_label(priority),
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
