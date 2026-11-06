"""Retrieval: KB articles, known issues, and similar incidents for one incident.

ServiceNow → incremental sync → index → small-model rerank → threshold.
Production index: Postgres full-text + pgvector (Voyage embeddings), fused with RRF.
Offline/test index: SQLite FTS5 (keyword only).
"""

from support_agent.retrieval.embeddings import EmbeddingError, VoyageEmbedder
from support_agent.retrieval.index import SearchBackend, SearchIndex, build_fts_query
from support_agent.retrieval.models import RetrievalResult, RetrievedDocument
from support_agent.retrieval.pg_index import IndexConfigError, PgSearchIndex
from support_agent.retrieval.reranker import Reranker, RetrievalError
from support_agent.retrieval.service import RetrievalService

__all__ = [
    "EmbeddingError",
    "IndexConfigError",
    "PgSearchIndex",
    "Reranker",
    "RetrievalError",
    "RetrievalResult",
    "RetrievalService",
    "RetrievedDocument",
    "SearchBackend",
    "SearchIndex",
    "VoyageEmbedder",
    "build_fts_query",
]
