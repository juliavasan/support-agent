"""Hybrid retrieval: Voyage embedder (fake HTTP), RRF fusion, degraded modes, and the
Postgres + pgvector index (opt-in: `pytest -m postgres` with docker compose running)."""

from __future__ import annotations

import hashlib
import os
from typing import Any
from unittest.mock import MagicMock

import pytest

from conftest import INCIDENT_RECORD, make_response
from support_agent.retrieval.embeddings import EmbeddingError, VoyageEmbedder
from support_agent.retrieval.index import SearchIndex
from support_agent.retrieval.models import Candidate, Chunk, IndexedDocument
from support_agent.retrieval.pg_index import (
    IndexConfigError,
    fuse_rankings,
    schema,
    vector_literal,
)
from support_agent.retrieval.reranker import RerankResult, Scored
from support_agent.retrieval.service import RetrievalService
from support_agent.servicenow import Incident

API_KEY = "pa-test-key-not-real"


def voyage_response(vectors: list[list[float]], status: int = 200) -> MagicMock:
    # Voyage may return items out of order; the client must sort by index.
    data = [{"embedding": v, "index": i} for i, v in enumerate(vectors)][::-1]
    return make_response(status, {"data": data, "usage": {"total_tokens": 10}})


def make_embedder(*responses: MagicMock, dimensions: int = 2) -> tuple[VoyageEmbedder, MagicMock]:
    session = MagicMock()
    session.post.side_effect = list(responses)
    return VoyageEmbedder("voyage-4", dimensions, api_key=API_KEY, session=session), session


# --- embedder ---------------------------------------------------------------------------


def test_documents_and_queries_use_their_input_type() -> None:
    embedder, session = make_embedder(
        voyage_response([[1.0, 0.0], [0.0, 1.0]]), voyage_response([[0.5, 0.5]])
    )
    assert embedder.embed_documents(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert embedder.embed_query("q") == [0.5, 0.5]

    first, second = (c.kwargs for c in session.post.call_args_list)
    assert first["json"] == {
        "input": ["a", "b"],
        "model": "voyage-4",
        "input_type": "document",
        "output_dimension": 2,
    }
    assert second["json"]["input_type"] == "query"
    assert first["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert first["timeout"] == 30.0


def test_documents_are_embedded_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("support_agent.retrieval.embeddings.BATCH_SIZE", 2)
    embedder, session = make_embedder(
        voyage_response([[1.0, 0.0]] * 2), voyage_response([[0.0, 1.0]])
    )
    assert len(embedder.embed_documents(["a", "b", "c"])) == 3
    assert [len(c.kwargs["json"]["input"]) for c in session.post.call_args_list] == [2, 1]


def test_batches_are_capped_by_estimated_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("support_agent.retrieval.embeddings.BATCH_TOKENS", 100)
    embedder, session = make_embedder(
        voyage_response([[1.0, 0.0]] * 2), voyage_response([[0.0, 1.0]])
    )
    texts = ["x" * 160, "y" * 160, "z" * 160]  # ~41 tokens each: two fit in 100
    assert len(embedder.embed_documents(texts)) == 3
    assert [len(c.kwargs["json"]["input"]) for c in session.post.call_args_list] == [2, 1]


def test_rate_limit_without_retry_after_waits_for_a_request_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []
    monkeypatch.setattr("support_agent.retrieval.embeddings.time.sleep", waits.append)
    limited = make_response(429)
    limited.headers = {}
    embedder, _ = make_embedder(limited, voyage_response([[1.0, 0.0]]))
    assert embedder.embed_query("q") == [1.0, 0.0]
    assert waits == [21.0]


def test_rate_limit_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("support_agent.retrieval.embeddings.time.sleep", lambda _: None)
    limited = make_response(429)
    limited.headers = {"Retry-After": "1"}
    embedder, session = make_embedder(limited, voyage_response([[1.0, 0.0]]))
    assert embedder.embed_query("q") == [1.0, 0.0]
    assert session.post.call_count == 2


@pytest.mark.parametrize(
    "response",
    [make_response(401), voyage_response([[1.0, 0.0, 0.0]]), voyage_response([])],
    ids=["http-error", "wrong-size", "missing-vector"],
)
def test_bad_responses_raise_without_leaking_the_key(response: MagicMock) -> None:
    embedder, _ = make_embedder(response)
    with pytest.raises(EmbeddingError) as excinfo:
        embedder.embed_query("q")
    assert API_KEY not in str(excinfo.value)


def test_missing_key_fails_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    session = MagicMock()
    embedder = VoyageEmbedder("voyage-4", 2, session=session)
    with pytest.raises(EmbeddingError, match="VOYAGE_API_KEY"):
        embedder.embed_query("q")
    session.post.assert_not_called()


# --- fusion and SQL helpers -------------------------------------------------------------


def test_rrf_rewards_documents_both_retrievers_found() -> None:
    fused = fuse_rankings({"keyword": ["a", "b", "c"], "vector": ["d", "b", "a"]})
    assert [key for key, _ in fused][:2] == ["a", "b"]  # in both lists
    assert {key for key, _ in fused} == {"a", "b", "c", "d"}
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 63)


def test_vector_literal_and_schema_validation() -> None:
    assert vector_literal([0.25, -1.0, 1e-9]) == "[0.25,-1,1e-09]"
    assert "VECTOR(1024)" in schema(1024)
    with pytest.raises(IndexConfigError):
        schema(1000)


# --- service: hybrid and degraded modes -------------------------------------------------


class EchoReranker:
    """Scores every candidate 9, so the service output shows what search returned."""

    def rerank(self, incident: Incident, candidates: list[Candidate]) -> RerankResult:
        return RerankResult([Scored(c, 9, "r") for c in candidates], "claude-haiku-5-5", 1, 1)


class StubIndex:
    """A SearchBackend that records the query vector it was given."""

    def __init__(self, vector: list[float] | None = None, error: Exception | None = None):
        self.vector, self.error = vector, error
        self.received: list[Any] = []
        self._sqlite = SearchIndex(":memory:")
        self._sqlite.upsert(
            IndexedDocument(
                key="kb:1",
                source="kb",
                kind="kb_article",
                sys_id="1",
                number="KB0010018",
                title="Exchange Rate Revaluation",
                updated_on="t",
            ),
            [Chunk(heading="", text="Reverse an exchange rate revaluation.")],
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sqlite, name)

    def embed_query(self, text: str) -> list[float] | None:
        if self.error:
            raise self.error
        return self.vector

    def search(
        self,
        text: str,
        kind: Any,
        limit: int = 10,
        exclude_numbers: Any = (),
        query_vector: Any = None,
    ) -> list[Candidate]:
        self.received.append(query_vector)
        hits = self._sqlite.search(text, kind, limit, exclude_numbers)
        if query_vector is not None:
            for hit in hits:
                hit.matched_by = ["keyword", "vector"]
        return hits


QUERY = {"number": "INC0010010", "short_description": "Reverse an Exchange Rate Revaluation"}


def test_hybrid_search_passes_the_query_vector_and_reports_matches() -> None:
    index = StubIndex(vector=[0.1, 0.2])
    result = RetrievalService(index, EchoReranker(), 7).retrieve(
        Incident.from_api({**INCIDENT_RECORD, **QUERY})
    )
    assert set(map(tuple, filter(None, index.received))) == {(0.1, 0.2)}
    assert result.search_mode == "hybrid (keyword + vector)"
    assert result.kb_articles[0].matched_by == ["keyword", "vector"]


def test_embedding_outage_falls_back_to_keyword_search() -> None:
    index = StubIndex(error=EmbeddingError("Voyage returned HTTP 503"))
    result = RetrievalService(index, EchoReranker(), 7).retrieve(
        Incident.from_api({**INCIDENT_RECORD, **QUERY})
    )
    assert index.received and all(v is None for v in index.received)
    assert result.search_mode == "keyword"
    assert [d.id for d in result.kb_articles] == ["KB0010018"]
    assert any("keyword search only" in w for w in result.warnings)


# --- Postgres + pgvector (opt-in) -------------------------------------------------------


class HashEmbedder:
    """Deterministic fake embeddings: a bag of hashed words, so texts that share words
    are close. Enough to exercise pgvector SQL without calling Voyage."""

    model = "fake-hash"
    dimensions = 256

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in text.lower().split():
            vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
        return vector if any(vector) else [1.0] + [0.0] * (self.dimensions - 1)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def pg_index() -> Any:
    from support_agent.retrieval.pg_index import PgSearchIndex

    url = os.environ.get("RETRIEVAL_DATABASE_URL")
    if not url:
        pytest.skip("RETRIEVAL_DATABASE_URL is not set")
    import psycopg

    # A throwaway schema, so tests never touch the real index tables.
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
        conn.execute("DROP SCHEMA IF EXISTS pytest CASCADE")
        conn.execute("CREATE SCHEMA pytest")
    index = PgSearchIndex(f"{url}?options=-csearch_path%3Dpytest,public", HashEmbedder())
    yield index
    index.close()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA pytest CASCADE")


def pg_doc(number: str, title: str, kind: str = "kb_article") -> IndexedDocument:
    source = "incident" if kind == "incident" else "kb"
    return IndexedDocument(
        key=f"{source}:{number}",
        source=source,
        kind=kind,
        sys_id=number,
        number=number,
        title=title,
        updated_on="t1",
    )


@pytest.mark.postgres
def test_pg_hybrid_search_finds_by_keyword_and_by_meaning(pg_index: Any) -> None:
    pg_index.upsert_many(
        [
            (
                pg_doc("KB0010018", "Exchange Rate Revaluation"),
                [Chunk(heading="Reversing", text="Cancel the journal entry to reverse it.")],
            ),
            (
                pg_doc("KB0010022", "Cost Center"),
                [Chunk(heading="", text="Cost center tree and allocation.")],
            ),
            (
                pg_doc("INC0010010", "Reverse exchange rate revaluation", "incident"),
                [Chunk(heading="Description", text="Need to reverse a revaluation.")],
            ),
        ]
    )
    query = "reverse exchange rate revaluation"
    hits = pg_index.search(query, "kb_article", query_vector=pg_index.embed_query(query))
    assert hits[0].document.number == "KB0010018"
    assert hits[0].matched_by == ["keyword", "vector"]
    assert pg_index.search(query, "incident", exclude_numbers=["INC0010010"]) == []
    assert pg_index.search("revaluation", "kb_article")[0].matched_by == ["keyword"]


@pytest.mark.postgres
def test_pg_versions_delete_and_count(pg_index: Any) -> None:
    pg_index.upsert(pg_doc("KB0010022", "Cost Center"), [Chunk(heading="", text="Tree.")])
    assert pg_index.versions("kb") == {"KB0010022": "t1"}
    assert pg_index.count("kb_article") == 1
    pg_index.delete("kb", ["KB0010022"])
    assert pg_index.count() == 0


@pytest.mark.postgres
def test_pg_refuses_to_mix_embedding_models(pg_index: Any) -> None:
    from support_agent.retrieval.pg_index import PgSearchIndex

    other = HashEmbedder()
    other.model = "another-model"
    url = os.environ["RETRIEVAL_DATABASE_URL"]
    with pytest.raises(IndexConfigError, match="--rebuild"):
        PgSearchIndex(f"{url}?options=-csearch_path%3Dpytest,public", other)
