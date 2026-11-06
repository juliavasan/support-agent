"""Search index interface, and the keyword-only backend: SQLite FTS5 (BM25).

The production backend is PgSearchIndex (pg_index.py: Postgres full-text + pgvector,
hybrid). This one needs no server or embedding API, so it runs the unit tests and
works offline. Either index is a cache of ServiceNow data the agent can already
read; deleting it is always safe (the next sync rebuilds it).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from support_agent.retrieval.models import Candidate, Chunk, IndexedDocument, Kind, Source

SCHEMA = """
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
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    key UNINDEXED, title, heading, text, tokenize = 'porter unicode61'
);
"""

STOPWORDS = frozenset(
    """
    a about after again all also am an and any are as at be been before being but by can
    cannot could did do does doing don dont for from had has have having he her here hi
    his how i if in into is it its just me more my no not now of on once only or other our
    out over please same she should so some still such than thank thanks that the their
    them then there these they this those through to too under until up very was we were
    what when where which while who why will with would you your yours
    """.split()
)
MAX_QUERY_TERMS = 40
SNIPPET_CHARS = 600


class SearchBackend(Protocol):
    """What sync and RetrievalService need from an index (SQLite or Postgres)."""

    def versions(self, source: Source) -> dict[str, str]: ...
    def upsert_many(self, items: Sequence[tuple[IndexedDocument, list[Chunk]]]) -> None: ...
    def delete(self, source: Source, sys_ids: Iterable[str]) -> None: ...
    def count(self, kind: Kind | None = None) -> int: ...
    def embed_query(self, text: str) -> list[float] | None: ...
    def search(
        self,
        text: str,
        kind: Kind,
        limit: int = 10,
        exclude_numbers: Iterable[str] = (),
        query_vector: Sequence[float] | None = None,
    ) -> list[Candidate]: ...


def query_terms(text: str) -> list[str]:
    """Distinct lowercase [a-z0-9] terms worth searching for, in order of appearance.

    Keeping only these characters means ticket text can't inject search operators
    (FTS5 NEAR, column filters, parentheses; tsquery &, !, <->) into either backend.
    """
    seen: dict[str, None] = {}
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(token) >= 3 and token not in STOPWORDS and not token.isdigit():
            seen.setdefault(token, None)
    return list(seen)[:MAX_QUERY_TERMS]


def build_fts_query(text: str) -> str:
    """A safe FTS5 query: quoted terms joined with OR. OR plus BM25 ranks documents
    that share the most rare terms."""
    return " OR ".join(f'"{term}"' for term in query_terms(text))


class SearchIndex:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def versions(self, source: Source) -> dict[str, str]:
        """sys_id -> updated_on for every indexed document of a source."""
        rows = self._db.execute(
            "SELECT sys_id, updated_on FROM documents WHERE source = ?", (source,)
        )
        return {row["sys_id"]: row["updated_on"] for row in rows}

    def upsert_many(self, items: Sequence[tuple[IndexedDocument, list[Chunk]]]) -> None:
        for document, chunks in items:
            self.upsert(document, chunks)

    def upsert(self, document: IndexedDocument, chunks: Iterable[Chunk]) -> None:
        with self._db:
            self._delete(document.key)
            self._db.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            self._db.executemany(
                "INSERT INTO chunks (key, title, heading, text) VALUES (?, ?, ?, ?)",
                [(document.key, document.title, c.heading, c.text) for c in chunks],
            )

    def delete(self, source: Source, sys_ids: Iterable[str]) -> None:
        with self._db:
            for sys_id in sys_ids:
                self._delete(f"{source}:{sys_id}")

    def _delete(self, key: str) -> None:
        self._db.execute("DELETE FROM documents WHERE key = ?", (key,))
        self._db.execute("DELETE FROM chunks WHERE key = ?", (key,))

    def count(self, kind: Kind | None = None) -> int:
        if kind is None:
            row = self._db.execute("SELECT COUNT(*) FROM documents").fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) FROM documents WHERE kind = ?", (kind,)
            ).fetchone()
        return int(row[0])

    def embed_query(self, text: str) -> list[float] | None:
        return None  # keyword-only backend

    def search(
        self,
        text: str,
        kind: Kind,
        limit: int = 10,
        exclude_numbers: Iterable[str] = (),
        query_vector: Sequence[float] | None = None,
    ) -> list[Candidate]:
        """Best chunk per document, ranked by BM25 (title weighted above body).
        query_vector is ignored: this backend has no embeddings."""
        query = build_fts_query(text)
        if not query:
            return []
        excluded = list(exclude_numbers)
        placeholders = ",".join("?" for _ in excluded) or "''"
        # bm25() weights per column: key (unindexed), title, heading, text.
        rows = self._db.execute(
            f"""
            SELECT d.*, c.heading AS chunk_heading, c.text AS chunk_text,
                   bm25(chunks, 0.0, 4.0, 2.0, 1.0) AS score
            FROM chunks c JOIN documents d ON d.key = c.key
            WHERE chunks MATCH ? AND d.kind = ? AND d.number NOT IN ({placeholders})
            ORDER BY score
            LIMIT ?
            """,  # noqa: S608 - placeholders are generated "?" markers, not user input
            (query, kind, *excluded, limit * 5),
        ).fetchall()
        best: dict[str, Candidate] = {}
        for row in rows:
            if row["key"] in best:
                continue
            document = IndexedDocument(**{name: row[name] for name in IndexedDocument.model_fields})
            best[row["key"]] = Candidate(
                document=document,
                heading=row["chunk_heading"],
                snippet=row["chunk_text"][:SNIPPET_CHARS],
                score=row["score"],
                matched_by=["keyword"],
            )
            if len(best) == limit:
                break
        return list(best.values())
