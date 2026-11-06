"""Sync ServiceNow incidents into the retrieval index, and optionally try a query.

Knowledge (docs and known issues) is ingested separately: scripts/ingest_kb.py.

Uses the same settings as the agent: RETRIEVAL_DATABASE_URL set → hybrid Postgres +
pgvector index (Voyage embeddings), otherwise the keyword-only SQLite index. Reads
ServiceNow as the agent's own account; never writes to ServiceNow.

Usage:
  python3 scripts/build_index.py                  incremental sync, print counts
  python3 scripts/build_index.py --rebuild        drop the index and re-embed everything
  python3 scripts/build_index.py --query "text"   then show what each retriever finds
"""

from __future__ import annotations

import argparse
import sys

from support_agent.config import load_agent_settings
from support_agent.graph.workflow import default_client_factory, default_index_factory
from support_agent.retrieval import PgSearchIndex
from support_agent.retrieval.service import KINDS
from support_agent.retrieval.sync import sync_incidents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rebuild", action="store_true", help="drop and rebuild the index")
    parser.add_argument("--query", help="search text to try after syncing")
    args = parser.parse_args()

    settings = load_agent_settings()
    index = default_index_factory(settings)
    backend = (
        f"Postgres + pgvector ({settings.embedding_model}, {settings.embedding_dimensions}d)"
        if isinstance(index, PgSearchIndex)
        else f"SQLite FTS5 ({settings.retrieval_db_path})"
    )
    print(f"Index: {backend}")
    if args.rebuild:
        if not isinstance(index, PgSearchIndex):
            settings.retrieval_db_path.unlink(missing_ok=True)
            index = default_index_factory(settings)
        else:
            index.reset()
        print("Index dropped.")

    client = default_client_factory()
    print(sync_incidents(client, index))
    print("By kind: " + ", ".join(f"{kind} {index.count(kind)}" for kind in KINDS))

    if args.query:
        vector = index.embed_query(args.query)
        for kind in KINDS:
            print(f"\n{kind}:")
            for hit in index.search(args.query, kind, limit=5, query_vector=vector):
                found = "+".join(hit.matched_by)
                print(f"  {hit.document.number:<34} [{found:<14}] {hit.document.title[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
