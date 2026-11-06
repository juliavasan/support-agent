"""Retrieval for one incident:
sync (optional) → embed query → hybrid search per kind → rerank → threshold.

Retrieval degrades instead of failing: if the embedding call fails, search is
keyword-only; if sync fails, the existing index is used. Each case adds a warning
that shows in the run output and the trace.
"""

from __future__ import annotations

from typing import Protocol

from support_agent.retrieval.embeddings import EmbeddingError
from support_agent.retrieval.index import SearchBackend
from support_agent.retrieval.models import Candidate, Kind, RetrievalResult, RetrievedDocument
from support_agent.retrieval.reranker import RerankResult
from support_agent.retrieval.sync import SyncClient, sync_incidents
from support_agent.servicenow import Incident, ServiceNowError

CANDIDATES_PER_KIND = 8
RESULTS_PER_KIND = 3
KINDS: tuple[Kind, ...] = ("kb_article", "known_issue", "incident")


class CandidateReranker(Protocol):
    def rerank(self, incident: Incident, candidates: list[Candidate]) -> RerankResult: ...


class RetrievalService:
    def __init__(
        self,
        index: SearchBackend,
        reranker: CandidateReranker,
        threshold: int,
        sync_client: SyncClient | None = None,
    ):
        self._index = index
        self._reranker = reranker
        self._threshold = threshold
        self._sync_client = sync_client

    def sync(self) -> list[str]:
        """Pick up new and changed incidents. Failures become warnings: a stale index
        still helps. Knowledge is refreshed by its own ingestion job (sources.py)."""
        if self._sync_client is None:
            return []
        warnings: list[str] = []
        try:
            sync_incidents(self._sync_client, self._index)
        except (ServiceNowError, EmbeddingError) as exc:
            warnings.append(f"Incident sync failed, using the existing index: {exc}")
        return warnings

    def retrieve(self, incident: Incident) -> RetrievalResult:
        warnings = self.sync()
        query = f"{incident.short_description}\n{incident.description}"
        try:
            vector = self._index.embed_query(query)  # None for a keyword-only index
        except EmbeddingError as exc:
            vector = None
            warnings.append(f"Query embedding failed, keyword search only: {exc}")
        candidates = [
            candidate
            for kind in KINDS
            for candidate in self._index.search(
                query,
                kind,
                CANDIDATES_PER_KIND,
                exclude_numbers=[incident.number],
                query_vector=vector,
            )
        ]
        result = RetrievalResult(
            candidates_considered=len(candidates),
            search_mode="hybrid (keyword + vector)" if vector is not None else "keyword",
            threshold=self._threshold,
            warnings=warnings,
        )
        if not candidates:
            if self._index.count() == 0:
                result.warnings.append(
                    "The search index is empty; run scripts/ingest_kb.py and scripts/build_index.py"
                )
            return result

        reranked = self._reranker.rerank(incident, candidates)  # may raise RetrievalError
        result.rerank_model = reranked.model
        result.input_tokens = reranked.input_tokens
        result.output_tokens = reranked.output_tokens

        kept = sorted(
            (s for s in reranked.scored if s.relevance >= self._threshold),
            key=lambda s: (-s.relevance, s.candidate.score),
        )
        groups: dict[Kind, list[RetrievedDocument]] = {kind: [] for kind in KINDS}
        for scored in kept:
            doc = scored.candidate.document
            if len(groups[doc.kind]) < RESULTS_PER_KIND:
                groups[doc.kind].append(
                    RetrievedDocument(
                        id=doc.number,
                        kind=doc.kind,
                        title=doc.title,
                        state=doc.state,
                        resolution=doc.resolution,
                        reference=doc.reference,
                        url=doc.url,
                        heading=scored.candidate.heading,
                        snippet=scored.candidate.snippet,
                        relevance=scored.relevance,
                        reason=scored.reason,
                        matched_by=scored.candidate.matched_by,
                    )
                )
        result.kb_articles = groups["kb_article"]
        result.known_issues = groups["known_issue"]
        result.similar_incidents = groups["incident"]
        return result
