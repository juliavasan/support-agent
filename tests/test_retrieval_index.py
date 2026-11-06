"""HTML parsing, the FTS5 index, and incremental sync. In-memory SQLite, fake ServiceNow."""

from __future__ import annotations

from typing import Any

import pytest

from support_agent.retrieval.html_text import extract_sections, html_to_text, sanitize_html
from support_agent.retrieval.index import SearchIndex, build_fts_query
from support_agent.retrieval.models import Chunk, IndexedDocument
from support_agent.retrieval.sync import chunk_text, incident_document, sync_incidents
from support_agent.servicenow import IndexIncident, RecordVersion

# --- HTML -------------------------------------------------------------------------------


def test_sections_split_at_headings_and_skip_navigation() -> None:
    markup = (
        "<p>Intro text.</p><nav>Menu Home</nav>"
        "<h2>Payments</h2><p>Allow Stale Exchange Rate lets old rates be used.</p>"
        "<table><tr><th>Field</th><th>Meaning</th></tr><tr><td>A</td><td>B</td></tr></table>"
        "<script>alert(1)</script><h3>Repost</h3><ul><li>One</li><li>Two</li></ul>"
    )
    sections = extract_sections(markup)
    assert [s.heading for s in sections] == ["", "Payments", "Repost"]
    assert sections[0].text == "Intro text."
    assert "Allow Stale Exchange Rate" in sections[1].text
    assert "Field | Meaning" in sections[1].text
    assert "Menu" not in html_to_text(markup)
    assert "alert" not in html_to_text(markup)
    assert sections[2].text == "One\nTwo"


def test_sanitizer_keeps_structure_and_drops_active_content() -> None:
    dirty = (
        '<div class="x"><h2 id="a" onclick="evil()">Title</h2>'
        '<p style="color:red">Text &amp; <b>bold</b> <a href="javascript:alert(1)">bad</a> '
        '<a href="https://docs.frappe.io/erpnext/x" target="_blank">good</a></p>'
        "<script>steal()</script><img src=x onerror=evil()><iframe>x</iframe></div>"
    )
    clean = sanitize_html(dirty)
    assert clean == (
        "<h2>Title</h2><p>Text &amp; <b>bold</b> <a>bad</a> "
        '<a href="https://docs.frappe.io/erpnext/x">good</a></p>'
    )


def test_sanitizer_escapes_text_that_looks_like_markup() -> None:
    assert sanitize_html("<p>1 &lt; 2 &lt;script&gt;</p>") == "<p>1 &lt; 2 &lt;script&gt;</p>"


# --- FTS query --------------------------------------------------------------------------


def test_fts_query_quotes_terms_and_drops_noise() -> None:
    query = build_fts_query("How do I reverse an Exchange Rate Revaluation? 2024 v16")
    assert query == '"reverse" OR "exchange" OR "rate" OR "revaluation" OR "v16"'


@pytest.mark.parametrize(
    "text",
    ['title:secret OR "x', "NEAR(a b) AND NOT c*", "a) OR (b", "^ORactive=true"],
)
def test_fts_query_cannot_inject_operators(text: str) -> None:
    index = SearchIndex(":memory:")
    # Any FTS5 syntax error would raise sqlite3.OperationalError here.
    assert index.search(text, "kb_article") == []
    query = build_fts_query(text)
    for term in query.split(" OR ") if query else []:
        assert term.startswith('"') and term.endswith('"')


def test_empty_query_returns_nothing() -> None:
    assert build_fts_query("a an the ?? 123") == ""
    assert SearchIndex(":memory:").search("the and", "kb_article") == []


# --- index ------------------------------------------------------------------------------


def doc(number: str, title: str, kind: str = "kb_article", **overrides: Any) -> IndexedDocument:
    source = "incident" if kind == "incident" else "kb"
    values: dict[str, Any] = {
        "key": f"{source}:{number.lower():0>32}",
        "source": source,
        "kind": kind,
        "sys_id": f"{number.lower():0>32}",
        "number": number,
        "title": title,
        "updated_on": "2026-10-27 10:00:00",
    }
    values.update(overrides)
    return IndexedDocument(**values)


@pytest.fixture
def index() -> SearchIndex:
    index = SearchIndex(":memory:")
    index.upsert(
        doc("KB0010018", "Exchange Rate Revaluation"),
        [
            Chunk(heading="", text="Revalue foreign currency accounts at period end."),
            Chunk(heading="Reversing", text="Cancel the journal entry to reverse a revaluation."),
        ],
    )
    index.upsert(doc("KB0010022", "Cost Center"), [Chunk(heading="", text="Cost center tree.")])
    index.upsert(
        doc("KB0010074", "Known issue: revaluation reverse fails", "known_issue"),
        [Chunk(heading="", text="Reverse of exchange rate revaluation raises an error.")],
    )
    index.upsert(
        doc("INC0010010", "How do I reverse an Exchange Rate Revaluation?", "incident"),
        [Chunk(heading="Description", text="Need to reverse a revaluation.")],
    )
    return index


def test_search_filters_by_kind_and_returns_best_chunk_per_document(index: SearchIndex) -> None:
    hits = index.search("reverse exchange rate revaluation", "kb_article")
    assert [h.document.number for h in hits] == ["KB0010018"]
    assert hits[0].heading == "Reversing"  # the chunk that matches best
    assert [h.document.number for h in index.search("revaluation", "known_issue")] == ["KB0010074"]


def test_search_excludes_the_incident_being_triaged(index: SearchIndex) -> None:
    query = "reverse exchange rate revaluation"
    assert index.search(query, "incident") != []
    assert index.search(query, "incident", exclude_numbers=["INC0010010"]) == []


def test_upsert_replaces_and_delete_removes(index: SearchIndex) -> None:
    updated = doc("KB0010022", "Cost Center", updated_on="2026-10-28 09:00:00")
    index.upsert(updated, [Chunk(heading="", text="Distributed cost center allocation.")])
    assert index.count("kb_article") == 2
    assert index.search("distributed", "kb_article")[0].document.updated_on.startswith("2026-10-28")
    assert index.search("tree", "kb_article") == []

    index.delete("kb", [updated.sys_id])
    assert index.search("distributed", "kb_article") == []
    assert updated.sys_id not in index.versions("kb")


# --- chunking and documents -------------------------------------------------------------


def test_chunk_text_respects_the_limit() -> None:
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(40)) + "\n" + "y" * 400
    chunks = chunk_text("H", text, limit=300)
    assert all(len(c.text) <= 300 for c in chunks)
    assert all(c.heading == "H" for c in chunks)
    assert "".join(c.text.replace("\n", "") for c in chunks) == text.replace("\n", "")


def test_incident_document_indexes_resolution_notes() -> None:
    incident = IndexIncident(
        sys_id="b" * 32,
        number="INC0000050",
        short_description="Email not syncing",
        description="Outlook stopped syncing.",
        state="Closed",
        close_notes="Re-created the mail profile.",
        updated_on="t1",
    )
    document, chunks = incident_document(incident)
    assert document.resolution == "Re-created the mail profile."
    assert [c.heading for c in chunks] == ["Description", "Resolution"]


# --- incremental sync -------------------------------------------------------------------


class FakeSyncClient:
    def __init__(self) -> None:
        self.incidents: dict[str, IndexIncident] = {}
        self.fetched: list[list[str]] = []

    def list_incident_versions(self) -> list[RecordVersion]:
        return [
            RecordVersion(sys_id=i.sys_id, updated_on=i.updated_on) for i in self.incidents.values()
        ]

    def get_index_incidents(self, sys_ids: list[str]) -> list[IndexIncident]:
        self.fetched.append(sys_ids)
        return [self.incidents[s] for s in sys_ids]


def add_incident(client: FakeSyncClient, n: int, updated: str = "t1") -> None:
    sys_id = f"{n:032x}"
    client.incidents[sys_id] = IndexIncident(
        sys_id=sys_id,
        number=f"INC{n:07d}",
        short_description=f"Advance on USD invoice {n}",
        updated_on=updated,
    )


def test_incident_sync_is_incremental_and_removes_vanished_incidents() -> None:
    client, index = FakeSyncClient(), SearchIndex(":memory:")
    for n in (1, 2, 3):
        add_incident(client, n)

    first = sync_incidents(client, index)
    assert (first.total, first.fetched, first.removed) == (3, 3, 0)
    assert index.search("advance usd invoice", "incident")[0].document.number.startswith("INC")

    second = sync_incidents(client, index)
    assert (second.fetched, second.removed) == (0, 0)

    add_incident(client, 2, updated="t2")  # edited
    del client.incidents[f"{3:032x}"]  # deleted or no longer visible
    third = sync_incidents(client, index)
    assert (third.total, third.fetched, third.removed) == (2, 1, 1)
    assert client.fetched[-1] == [f"{2:032x}"]
    assert index.count("incident") == 2
