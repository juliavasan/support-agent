"""Knowledge sources indexed directly from where they are published.

  - ERPNext documentation pages (docs.frappe.io)  → kind "kb_article"
  - Open ERPNext bug reports (GitHub issues)        → kind "known_issue"

Fetching happens in scripts/ingest_kb.py; this module only turns fetched content into
indexed documents, so it is pure and unit-tested. Page and issue text is untrusted
input: it is parsed to plain text here and stays delimited as data in every prompt.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Sequence
from typing import Any

from support_agent.retrieval.html_text import extract_sections
from support_agent.retrieval.index import SearchBackend
from support_agent.retrieval.models import Chunk, IndexedDocument
from support_agent.retrieval.sync import SyncStats, chunk_text

UPSTREAM_REPO = "frappe/erpnext"
MAX_ISSUE_BODY_CHARS = 6000
INGEST_GROUP = 8  # documents per embed-and-write transaction

Item = tuple[IndexedDocument, list[Chunk]]


class SourceFormatError(ValueError):
    """A fetched page doesn't have the expected layout (the site changed)."""


def _version(chunks: list[Chunk]) -> str:
    """Content hash: a page is re-embedded only when its text actually changes."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(f"{chunk.heading}\0{chunk.text}\0".encode())
    return "sha256:" + digest.hexdigest()[:16]


def docs_document(url: str, slug: str, page_html: str) -> Item:
    """One docs.frappe.io page: its main content, split at headings."""
    title_match = re.search(r'id="wiki-page-title"[^>]*>([^<]+)<', page_html)
    start = page_html.find('id="wiki-content"')
    end = page_html.find('id="wiki-nav-buttons"', start)
    if not title_match or start < 0 or end < 0:
        raise SourceFormatError(f"Unexpected page layout: {url}")
    title = html.unescape(title_match.group(1)).strip()
    # From the end of the content element's opening tag to the start of the tag that
    # carries the nav-buttons id.
    body = page_html[page_html.index(">", start) + 1 : page_html.rindex("<", 0, end)]
    chunks = [
        chunk
        for section in extract_sections(body)
        for chunk in chunk_text(section.heading, section.text)
    ] or [Chunk(heading="", text=title)]
    number = f"docs/{slug}"
    document = IndexedDocument(
        key=f"kb:{number}",
        source="kb",
        kind="kb_article",
        sys_id=number,
        number=number,
        title=title,
        url=url,
        updated_on=_version(chunks),
    )
    return document, chunks


def markdown_text(markdown: str) -> str:
    """Issue markdown to plain text: drop template comments and images, keep the rest."""
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)|<img[^>]*>", "[image]", text)
    text = re.sub(r"```[^\n]*\n?", "", text)
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def issue_document(issue: dict[str, Any]) -> Item:
    """One open GitHub issue as a known issue, linked to its upstream report."""
    number = int(issue["number"])
    body = markdown_text(issue.get("body") or "")
    if len(body) > MAX_ISSUE_BODY_CHARS:
        body = body[:MAX_ISSUE_BODY_CHARS] + "\n[truncated]"
    labels = ", ".join(label["name"] for label in issue.get("labels", []))
    chunks = chunk_text("Reported problem", body) or [Chunk(heading="", text=issue["title"])]
    document = IndexedDocument(
        key=f"kb:erpnext#{number}",
        source="kb",
        kind="known_issue",
        sys_id=f"erpnext#{number}",
        number=f"erpnext#{number}",
        title=issue["title"].strip(),
        state=f"open upstream bug ({labels})" if labels else "open upstream bug",
        reference=f"{UPSTREAM_REPO}#{number}",
        url=issue["html_url"],
        updated_on=issue["updated_at"],
    )
    return document, chunks


def ingest(index: SearchBackend, items: Sequence[Item]) -> SyncStats:
    """Make the index's knowledge documents match `items`: embed new or changed ones,
    remove ones that are gone (a page dropped from the source list, a bug now closed)."""
    indexed = index.versions("kb")
    changed = [item for item in items if indexed.get(item[0].sys_id) != item[0].updated_on]
    # Commit in small groups: if embedding fails partway, finished groups are kept and
    # the next run only embeds what is left.
    for start in range(0, len(changed), INGEST_GROUP):
        index.upsert_many(changed[start : start + INGEST_GROUP])
    gone = set(indexed) - {document.sys_id for document, _ in items}
    index.delete("kb", gone)
    return SyncStats("kb", total=len(items), fetched=len(changed), removed=len(gone))
