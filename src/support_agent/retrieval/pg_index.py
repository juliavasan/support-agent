"""Hybrid search index in Postgres: full-text search plus pgvector, fused with RRF.

The production shape of retrieval. One database holds both signals for every chunk:
  - keyword: a weighted tsvector (title > heading > body) ranked with ts_rank_cd;
    strong on exact ERP terms, field names, and error strings.
  - vector: a Voyage embedding in a pgvector column with an HNSW index (cosine);
    strong on paraphrase ("got charged twice" vs "duplicate payment entry").
Each signal returns its best chunk per document; Reciprocal Rank Fusion merges the
two rankings without having to calibrate their scores against each other. The LLM
reranker then judges the fused candidates.

Like the SQLite index this is a cache of ServiceNow data the agent can already read;
dropping it is safe (the next sync rebuilds it, re-embedding every chunk).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from support_agent.retrieval.embeddings import Embedder
from support_agent.retrieval.index import SNIPPET_CHARS, query_terms
from support_agent.retrieval.models import (
    Candidate,
    Chunk,
    IndexedDocument,
    Kind,
    MatchedBy,
    Source,
)

RRF_K = 60  # the constant from the original RRF paper; damps the weight of top ranks
SUPPORTED_DIMENSIONS = (256, 512, 1024, 2048)


class IndexConfigError(RuntimeError):
    """The database holds an index built with a different embedding setup."""


def schema(dimensions: int) -> str:
    if dimensions not in SUPPORTED_DIMENSIONS:
        raise IndexConfigError(f"Unsupported embedding dimensions: {dimensions}")
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS documents (
        key TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        kind TEXT NOT NULL,
        sys_id TEXT NOT NULL,
        number TEXT NOT NULL,
        title TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT '',
        resolution TEXT NOT NULL DEFAULT '',
        reference TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        updated_on TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chunks (
        id BIGSERIAL PRIMARY KEY,
        key TEXT NOT NULL REFERENCES documents (key) ON DELETE CASCADE,
        title TEXT NOT NULL,
        heading TEXT NOT NULL,
        text TEXT NOT NULL,
        tsv TSVECTOR GENERATED ALWAYS AS (
            setweight(to_tsvector('english', title), 'A')
            || setweight(to_tsvector('english', heading), 'B')
            || setweight(to_tsvector('english', text), 'D')
        ) STORED,
        embedding VECTOR({dimensions}) NOT NULL
    );
    CREATE INDEX IF NOT EXISTS chunks_key ON chunks (key);
    CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING GIN (tsv);
    CREATE INDEX IF NOT EXISTS chunks_embedding ON chunks
        USING HNSW (embedding vector_cosine_ops);
    """  # noqa: S608 - dimensions is validated against a fixed set above


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text format; avoids a client-side adapter dependency."""
    return "[" + ",".join(f"{value:.7g}" for value in vector) + "]"


def embedding_text(document: IndexedDocument, chunk: Chunk) -> str:
    # The title and heading give a short chunk the context it needs to embed well.
    return "\n".join(part for part in (document.title, chunk.heading, chunk.text) if part)


def fuse_rankings(rankings: dict[str, list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(d) = sum over rankings of 1 / (k + rank).

    Returns (document key, score) best first. A document found by both retrievers
    beats one found by only one at a similar rank.
    """
    scores: dict[str, float] = {}
    for ranking in rankings.values():
        for rank, key in enumerate(ranking, 1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: -item[1])


class PgSearchIndex:
    def __init__(self, conninfo: str, embedder: Embedder):
        self._embedder = embedder
        self._db = psycopg.connect(conninfo, row_factory=dict_row, autocommit=True)
        self._db.execute(schema(embedder.dimensions))
        self._check_embedding_setup()

    def close(self) -> None:
        self._db.close()

    def _check_embedding_setup(self) -> None:
        """Vectors from different models or sizes can't be compared; refuse to mix them."""
        wanted = f"{self._embedder.model}/{self._embedder.dimensions}"
        row = self._db.execute("SELECT value FROM index_meta WHERE key = 'embedding'").fetchone()
        if row is None:
            self._db.execute("INSERT INTO index_meta VALUES ('embedding', %s)", (wanted,))
        elif row["value"] != wanted:
            raise IndexConfigError(
                f"The index was built with {row['value']}, settings ask for {wanted}. "
                "Run scripts/build_index.py --rebuild."
            )

    def reset(self) -> None:
        """Drop everything, including the stored embedding setup."""
        self._db.execute("DROP TABLE IF EXISTS chunks, documents, index_meta")
        self._db.execute(schema(self._embedder.dimensions))
        self._check_embedding_setup()

    def versions(self, source: Source) -> dict[str, str]:
        rows = self._db.execute(
            "SELECT sys_id, updated_on FROM documents WHERE source = %s", (source,)
        )
        return {row["sys_id"]: row["updated_on"] for row in rows}

    def upsert(self, document: IndexedDocument, chunks: Iterable[Chunk]) -> None:
        self.upsert_many([(document, list(chunks))])

    def upsert_many(self, items: Sequence[tuple[IndexedDocument, list[Chunk]]]) -> None:
        """Embed every chunk in batches first, then write in one transaction, so a
        failed embedding call leaves the index unchanged (and the next sync retries)."""
        pairs = [(doc, chunk) for doc, chunks in items for chunk in chunks]
        vectors = self._embedder.embed_documents([embedding_text(d, c) for d, c in pairs])
        with self._db.transaction():
            for document, _ in items:
                self._db.execute("DELETE FROM documents WHERE key = %s", (document.key,))
                self._db.execute(
                    "INSERT INTO documents VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        document.key,
                        document.source,
                        document.kind,
                        document.sys_id,
                        document.number,
                        document.title,
                        document.state,
                        document.resolution,
                        document.reference,
                        document.url,
                        document.updated_on,
                    ),
                )
            with self._db.cursor() as cur:
                cur.executemany(
                    "INSERT INTO chunks (key, title, heading, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s::vector)",
                    [
                        (doc.key, doc.title, chunk.heading, chunk.text, vector_literal(vector))
                        for (doc, chunk), vector in zip(pairs, vectors, strict=True)
                    ],
                )

    def delete(self, source: Source, sys_ids: Iterable[str]) -> None:
        keys = [f"{source}:{sys_id}" for sys_id in sys_ids]
        if keys:
            self._db.execute("DELETE FROM documents WHERE key = ANY(%s)", (keys,))

    def count(self, kind: Kind | None = None) -> int:
        if kind is None:
            row = self._db.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE kind = %s", (kind,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def embed_query(self, text: str) -> list[float]:
        return self._embedder.embed_query(text)

    def search(
        self,
        text: str,
        kind: Kind,
        limit: int = 10,
        exclude_numbers: Iterable[str] = (),
        query_vector: Sequence[float] | None = None,
    ) -> list[Candidate]:
        """Top documents by RRF over keyword and vector rankings (best chunk each).

        Without a query vector (embedding failed) this is keyword-only search.
        """
        excluded = list(exclude_numbers)
        hits: dict[MatchedBy, dict[str, dict[str, Any]]] = {}
        terms = query_terms(text)
        if terms:
            # Terms are [a-z0-9] only, so the OR-joined string is a valid, inert tsquery.
            hits["keyword"] = self._best_per_document(
                """
                SELECT d.*, c.heading AS chunk_heading, c.text AS chunk_text,
                       ts_rank_cd(c.tsv, q) AS score
                FROM chunks c JOIN documents d ON d.key = c.key,
                     to_tsquery('english', %(query)s) q
                WHERE c.tsv @@ q AND d.kind = %(kind)s AND NOT d.number = ANY(%(excluded)s)
                ORDER BY score DESC
                LIMIT %(rows)s
                """,
                {"query": " | ".join(terms), "kind": kind, "excluded": excluded},
                limit,
            )
        if query_vector is not None:
            hits["vector"] = self._best_per_document(
                """
                SELECT d.*, c.heading AS chunk_heading, c.text AS chunk_text,
                       c.embedding <=> %(vector)s::vector AS score
                FROM chunks c JOIN documents d ON d.key = c.key
                WHERE d.kind = %(kind)s AND NOT d.number = ANY(%(excluded)s)
                ORDER BY score
                LIMIT %(rows)s
                """,
                {"vector": vector_literal(query_vector), "kind": kind, "excluded": excluded},
                limit,
            )

        fused = fuse_rankings({name: list(rows) for name, rows in hits.items()})[:limit]
        candidates = []
        for key, rrf in fused:
            matched_by = [name for name, rows in hits.items() if key in rows]
            # The chunk shown to the reranker: the keyword hit if any (it contains the
            # query terms), otherwise the closest chunk by meaning.
            row = next(
                hits[name][key] for name in ("keyword", "vector") if key in hits.get(name, {})
            )
            document = IndexedDocument(**{name: row[name] for name in IndexedDocument.model_fields})
            candidates.append(
                Candidate(
                    document=document,
                    heading=row["chunk_heading"],
                    snippet=row["chunk_text"][:SNIPPET_CHARS],
                    score=-rrf,
                    matched_by=matched_by,
                )
            )
        return candidates

    def _best_per_document(
        self, sql: str, params: dict[str, Any], limit: int
    ) -> dict[str, dict[str, Any]]:
        """Rows ranked best first, keeping only each document's best chunk (dicts keep
        insertion order, so the result is the document ranking)."""
        rows = self._db.execute(sql, {**params, "rows": limit * 5}).fetchall()
        best: dict[str, dict[str, Any]] = {}
        for row in rows:
            best.setdefault(row["key"], row)
            if len(best) == limit:
                break
        return best
