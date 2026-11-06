"""Schemas for the LLM triage analysis.

`TriageAnalysis` is the exact structured output requested from the model. It has no
priority field on purpose: priority is computed from impact and urgency by the
configurable matrix (`priority.py`), so the model cannot invent one. Document IDs it
cites are checked against the retrieved set (`analyzer.py`), so it cannot invent those
either.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from support_agent.analysis.customer_tiers import PriorityAdjustment

Level = Literal["High", "Medium", "Low"]
Category = Literal[
    "Configuration",
    "Permissions / Access",
    "Data",
    "Integration",
    "Application Bug",
    "Other / Unknown",
]


class TriageAnalysis(BaseModel):
    """Structured output from the model."""

    summary: str = Field(description="One or two sentences describing the reported issue.")
    impact: Level = Field(description="How many users or how much business is affected.")
    impact_reason: str = Field(description="One sentence justifying the impact estimate.")
    urgency: Level = Field(description="How quickly the issue needs resolution.")
    urgency_reason: str = Field(description="One sentence justifying the urgency estimate.")
    category: Category = Field(description="The single best-fitting issue category.")
    potential_causes: list[str] = Field(
        description="Plausible causes. Each is a hypothesis to verify, not a conclusion."
    )
    troubleshooting_steps: list[str] = Field(
        description=(
            "Ordered first steps a support engineer should take to confirm or rule out causes."
        )
    )
    missing_information: list[str] = Field(
        description="Specific details the caller has not provided that would help. Empty if none."
    )
    relevant_documents: list[str] = Field(
        description=(
            "IDs of retrieved documents (KB articles, known issues, incidents) that genuinely "
            "informed this assessment, e.g. KB0010012 or INC0010003. Only IDs that appear in "
            "<retrieved_documents>. Empty if none were provided or none helped."
        )
    )
    possible_match_id: str = Field(
        description=(
            "The ID of a retrieved known issue or incident that likely describes the same "
            "problem, or an empty string if none does."
        )
    )
    possible_match_reason: str = Field(
        description="One sentence on why it matches, or an empty string if there is no match."
    )


class TriageAssessment(BaseModel):
    """The analysis plus everything the agent adds deterministically."""

    analysis: TriageAnalysis
    # Recommended priority: the matrix result, then the customer-tier rule if applied.
    priority: int = Field(ge=1, le=5)
    priority_label: str
    # Set by the apply_customer_policy node; base_priority there is the matrix result.
    customer_adjustment: PriorityAdjustment | None = None
    # Document IDs the model cited that were not in the retrieved set. Removed from
    # the analysis, kept here for the trace.
    dropped_references: list[str] = Field(default_factory=list)
    model: str  # the model that produced the analysis (after any server-side fallback)
    input_tokens: int = 0
    output_tokens: int = 0
