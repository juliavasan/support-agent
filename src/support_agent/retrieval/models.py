"""Retrieval data models: what is indexed, what search returns, what the graph keeps."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["kb", "incident"]
# kb_article: how-to / reference docs. known_issue: an open upstream defect.
# incident: a past or open ServiceNow incident.
Kind = Literal["kb_article", "known_issue", "incident"]


class IndexedDocument(BaseModel):
    key: str  # "<source>:<sys_id>"
    source: Source
    kind: Kind
    sys_id: str
    # The id shown to the model and in notes: INC0010001, docs/payment-entry, erpnext#62718.
    # sys_id is the ServiceNow sys_id for incidents and the same id for knowledge.
    number: str
    title: str
    state: str = ""  # incidents: New / In Progress / Resolved ...; KB: workflow state
    resolution: str = ""  # incidents: close notes
    reference: str = ""  # known issues: upstream issue, e.g. "frappe/erpnext#62718"
    url: str = ""  # docs page or upstream issue
    # Change marker for incremental indexing: sys_updated_on for incidents, a content
    # hash for docs pages, GitHub's updated_at for issues.
    updated_on: str


class Chunk(BaseModel):
    heading: str
    text: str


MatchedBy = Literal["keyword", "vector"]


class Candidate(BaseModel):
    """A search hit: the best-matching chunk of one document."""

    document: IndexedDocument
    heading: str
    snippet: str
    # Lower is better: BM25 in SQLite FTS5, negative RRF score in the hybrid index.
    score: float
    matched_by: list[MatchedBy] = Field(default_factory=list)


class RetrievedDocument(BaseModel):
    """A reranked hit above the relevance threshold, as kept in graph state."""

    id: str  # the number shown to the model and in the note
    kind: Kind
    title: str
    state: str = ""
    resolution: str = ""
    reference: str = ""
    url: str = ""
    heading: str = ""
    snippet: str
    relevance: int = Field(ge=0, le=10)
    reason: str = ""
    matched_by: list[MatchedBy] = Field(default_factory=list)  # which retrievers found it


class RetrievalResult(BaseModel):
    kb_articles: list[RetrievedDocument] = Field(default_factory=list)
    known_issues: list[RetrievedDocument] = Field(default_factory=list)
    similar_incidents: list[RetrievedDocument] = Field(default_factory=list)
    candidates_considered: int = 0
    search_mode: str = ""  # "hybrid (keyword + vector)" or "keyword"
    threshold: int = 0
    rerank_model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: list[str] = Field(default_factory=list)

    def documents(self) -> list[RetrievedDocument]:
        return [*self.kb_articles, *self.known_issues, *self.similar_incidents]

    def ids(self) -> set[str]:
        return {doc.id for doc in self.documents()}
