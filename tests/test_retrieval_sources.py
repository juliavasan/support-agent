"""Knowledge sources: docs pages and GitHub issues become indexed documents, and
ingestion keeps the index in step with the source. No network."""

from __future__ import annotations

from typing import Any

import pytest

from support_agent.retrieval.index import SearchIndex
from support_agent.retrieval.sources import (
    SourceFormatError,
    docs_document,
    ingest,
    issue_document,
    markdown_text,
)

URL = "https://docs.frappe.io/erpnext/exchange-rate-revaluation"


def page(body: str, title: str = "Exchange Rate Revaluation") -> str:
    return (
        "<html><nav>Docs menu</nav>"
        f'<h1 id="wiki-page-title" class="t">{title}</h1>'
        f'<div id="wiki-content" class="c">{body}</div>'
        '<div id="wiki-nav-buttons">Previous Next</div></html>'
    )


def issue(number: int = 62718, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "number": number,
        "title": " Multiple credit notes for one Sales Order ",
        "body": "<!-- template -->\n### Steps\n1. Create a return\n\n![shot](x.png)",
        "labels": [{"name": "accounts"}, {"name": "bug"}],
        "html_url": f"https://github.com/frappe/erpnext/issues/{number}",
        "updated_at": "2026-10-20T08:00:00Z",
    }
    values.update(overrides)
    return values


def test_docs_page_keeps_main_content_split_at_headings() -> None:
    document, chunks = docs_document(
        URL,
        "exchange-rate-revaluation",
        page("<p>Revalue at period end.</p><h2>Reversing</h2><p>Cancel the entry.</p>"),
    )
    assert document.number == "docs/exchange-rate-revaluation"
    assert (document.kind, document.title, document.url) == (
        "kb_article",
        "Exchange Rate Revaluation",
        URL,
    )
    assert [(c.heading, c.text) for c in chunks] == [
        ("", "Revalue at period end."),
        ("Reversing", "Cancel the entry."),
    ]
    assert "menu" not in " ".join(c.text for c in chunks).lower()


def test_docs_version_changes_only_when_the_text_changes() -> None:
    a, _ = docs_document(URL, "x", page("<p>One.</p>"))
    b, _ = docs_document(URL, "x", page("<p>One.</p>"))
    c, _ = docs_document(URL, "x", page("<p>Two.</p>"))
    assert a.updated_on == b.updated_on != c.updated_on


def test_unexpected_page_layout_is_reported() -> None:
    with pytest.raises(SourceFormatError):
        docs_document(URL, "x", "<html><p>No wiki content here</p></html>")


def test_issue_becomes_a_linked_known_issue() -> None:
    document, chunks = issue_document(issue())
    assert document.number == "erpnext#62718"
    assert document.kind == "known_issue"
    assert document.title == "Multiple credit notes for one Sales Order"
    assert document.reference == "frappe/erpnext#62718"
    assert document.url.endswith("/issues/62718")
    assert document.state == "open upstream bug (accounts, bug)"
    assert document.updated_on == "2026-10-20T08:00:00Z"
    assert chunks[0].heading == "Reported problem"
    assert "template" not in chunks[0].text and "[image]" in chunks[0].text


def test_markdown_text_drops_noise() -> None:
    assert markdown_text("## Title\n```py\nx = 1\n```\n\n\n\nEnd") == "Title\nx = 1\n\nEnd"


def test_ingest_embeds_changes_only_and_removes_closed_issues() -> None:
    index = SearchIndex(":memory:")
    docs = docs_document(URL, "exchange-rate-revaluation", page("<p>Revalue.</p>"))

    first = ingest(index, [docs, issue_document(issue(1)), issue_document(issue(2))])
    assert (first.total, first.fetched, first.removed) == (3, 3, 0)

    second = ingest(index, [docs, issue_document(issue(1)), issue_document(issue(2))])
    assert (second.fetched, second.removed) == (0, 0)

    edited = issue_document(issue(1, updated_at="2026-11-01T00:00:00Z"))
    third = ingest(index, [docs, edited])  # issue 2 was closed upstream
    assert (third.total, third.fetched, third.removed) == (2, 1, 1)
    assert index.count("known_issue") == 1
    hits = index.search("credit notes sales order", "known_issue")
    assert [h.document.number for h in hits] == ["erpnext#1"]
