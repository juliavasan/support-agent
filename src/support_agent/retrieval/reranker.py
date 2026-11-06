"""Rerank BM25 candidates with a small Claude model and structured output.

Candidates are shown to the model under opaque ids (C1, C2, ...), not their KB or
incident numbers, so a score can only attach to a candidate we actually retrieved.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
from pydantic import BaseModel, Field

from support_agent.config import AgentSettings
from support_agent.llm import LLMError, default_llm
from support_agent.prompts import load_prompt
from support_agent.retrieval.models import Candidate
from support_agent.servicenow.models import Incident

MAX_TOKENS = 4000
INCIDENT_CHARS = 3000


class RetrievalError(RuntimeError):
    """Retrieval or reranking failed. Messages never contain credentials."""


class CandidateScore(BaseModel):
    candidate_id: str = Field(description="The candidate id exactly as given, e.g. C3.")
    relevance: int = Field(description="Integer 0-10 per the rubric.")
    reason: str = Field(description="One sentence: what matches or what differs.")


class RerankOutput(BaseModel):
    scores: list[CandidateScore]


@dataclass
class Scored:
    candidate: Candidate
    relevance: int
    reason: str


@dataclass
class RerankResult:
    scored: list[Scored]
    model: str
    input_tokens: int
    output_tokens: int


def _escape(text: str) -> str:
    # Stops data from closing our wrappers and posing as instructions.
    for tag in ("incident", "candidate"):
        text = text.replace(f"<{tag}", f"&lt;{tag}").replace(f"</{tag}", f"&lt;/{tag}")
    return text


def build_rerank_message(incident: Incident, candidates: list[Candidate]) -> str:
    body = f"{incident.short_description}\n\n{incident.description}"[:INCIDENT_CHARS]
    parts = [
        "Score each candidate for the incident below.",
        "",
        f"<incident>\n{_escape(body.strip())}\n</incident>",
        "",
    ]
    for i, candidate in enumerate(candidates, 1):
        doc = candidate.document
        section = f"Section: {_escape(candidate.heading)}\n" if candidate.heading else ""
        parts.append(
            f'<candidate id="C{i}" type="{doc.kind}">\n'
            f"Title: {_escape(doc.title)}\n{section}{_escape(candidate.snippet)}\n</candidate>"
        )
    return "\n".join(parts)


class Reranker:
    def __init__(self, settings: AgentSettings, client: anthropic.Anthropic | None = None):
        self._model = settings.rerank_model
        self._llm = default_llm(settings, client)
        self._system = load_prompt("rerank_system")

    def rerank(self, incident: Incident, candidates: list[Candidate]) -> RerankResult:
        if not candidates:
            return RerankResult([], self._model, 0, 0)
        try:
            response = self._llm.parse(
                model=self._model,
                system=self._system,
                user=build_rerank_message(incident, candidates),
                output_format=RerankOutput,
                max_tokens=MAX_TOKENS,
                effort="low",
            )
        except LLMError as exc:
            raise RetrievalError(str(exc)) from None
        if response.stop_reason in ("refusal", "max_tokens") or response.output is None:
            raise RetrievalError(f"Reranking returned no usable output ({response.stop_reason})")

        by_id = {f"C{i}": c for i, c in enumerate(candidates, 1)}
        scored: dict[str, Scored] = {}
        for score in response.output.scores:
            candidate = by_id.get(score.candidate_id.strip())
            # Ignore unknown ids and repeats: a score must map to one retrieved candidate.
            if candidate is None or score.candidate_id in scored:
                continue
            relevance = max(0, min(10, score.relevance))
            scored[score.candidate_id] = Scored(candidate, relevance, score.reason.strip())
        return RerankResult(
            scored=list(scored.values()),
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
