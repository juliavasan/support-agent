"""Ingest the knowledge base into the retrieval index, straight from the sources.

  - ERPNext docs pages listed in data/seed/kb_sources.json (docs.frappe.io)
  - Open ERPNext bugs matching the GitHub search query in the same file (known issues)

Run on a schedule in production (a nightly job); here, by hand. Pages are cached in
data/kb_cache/ (gitignored) so repeated runs don't re-download; --refresh re-fetches.
Only new or changed documents are embedded, and documents that disappeared from the
source (a closed bug) are removed. Never writes to ServiceNow.

Usage: python3 scripts/ingest_kb.py [--refresh] [--query "text to try"]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from support_agent.config import PROJECT_ROOT, load_agent_settings
from support_agent.graph.workflow import default_index_factory
from support_agent.retrieval.service import KINDS
from support_agent.retrieval.sources import (
    Item,
    SourceFormatError,
    docs_document,
    ingest,
    issue_document,
)

SOURCES = PROJECT_ROOT / "data" / "seed" / "kb_sources.json"
CACHE_DIR = PROJECT_ROOT / "data" / "kb_cache"
USER_AGENT = "support-agent-kb-ingest/0.1 (+https://github.com/juliavasan/support-agent)"
FETCH_DELAY_SECONDS = 1.0  # be polite to docs.frappe.io


def fetch_page(url: str, path: Path, refresh: bool) -> str:
    if path.exists() and not refresh:
        return path.read_text()
    time.sleep(FETCH_DELAY_SECONDS)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text)
    return resp.text


def open_issues(repo: str, query: str, limit: int, refresh: bool) -> list[dict[str, Any]]:
    path = CACHE_DIR / "known_issues.json"
    if path.exists() and not refresh:
        items: list[dict[str, Any]] = json.loads(path.read_text())
        return items[:limit]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(
        "https://api.github.com/search/issues",
        params={
            "q": f"repo:{repo} {query}",
            "sort": "created",
            "order": "desc",
            "per_page": str(min(limit, 100)),
        },
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    items = [i for i in resp.json()["items"] if "pull_request" not in i]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=1))
    return items[:limit]


def collect(refresh: bool) -> tuple[list[Item], int]:
    sources = json.loads(SOURCES.read_text())
    items: list[Item] = []
    failures = 0
    docs = sources["docs"]
    for slug in docs["pages"]:
        url = f"{docs['base_url']}{slug}"
        try:
            page = fetch_page(url, CACHE_DIR / "docs" / f"{slug}.html", refresh)
            items.append(docs_document(url, slug, page))
        except (requests.RequestException, SourceFormatError) as exc:
            failures += 1
            print(f"  ! {slug}: {exc}")
    cfg = sources["known_issues"]
    for issue in open_issues(cfg["repo"], cfg["query"], cfg["limit"], refresh):
        items.append(issue_document(issue))
    return items, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download pages and issues")
    parser.add_argument("--query", help="search text to try after ingesting")
    args = parser.parse_args()

    settings = load_agent_settings()
    index = default_index_factory(settings)
    items, failures = collect(args.refresh)
    docs = sum(1 for doc, _ in items if doc.kind == "kb_article")
    print(f"Collected {docs} docs pages and {len(items) - docs} open bugs")
    print(ingest(index, items))
    print("By kind: " + ", ".join(f"{kind} {index.count(kind)}" for kind in KINDS))

    if args.query:
        vector = index.embed_query(args.query)
        for kind in KINDS:
            print(f"\n{kind}:")
            for hit in index.search(args.query, kind, limit=5, query_vector=vector):
                found = "+".join(hit.matched_by)
                print(f"  {hit.document.number:<34} [{found:<14}] {hit.document.title[:60]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
