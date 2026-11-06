"""Incremental sync of ServiceNow incidents into the index, as the agent's own account.

Syncing as `support_agent` means ServiceNow ACLs decide which incidents can be
indexed. Each sync lists (sys_id, updated) for every readable incident, fetches only
new or changed ones, and removes ones that disappeared (deleted or no longer visible).
Knowledge (docs and known issues) is ingested from its sources instead (sources.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from support_agent.retrieval.index import SearchBackend
from support_agent.retrieval.models import Chunk, IndexedDocument, Source
from support_agent.servicenow import IndexIncident, RecordVersion

CHUNK_CHARS = 1500


class SyncClient(Protocol):
    def list_incident_versions(self) -> list[RecordVersion]: ...
    def get_index_incidents(self, sys_ids: list[str]) -> list[IndexIncident]: ...


@dataclass
class SyncStats:
    source: Source
    total: int = 0
    fetched: int = 0
    removed: int = 0

    def __str__(self) -> str:
        return (
            f"{self.source}: {self.total} indexed ({self.fetched} fetched, {self.removed} removed)"
        )


def chunk_text(heading: str, text: str, limit: int = CHUNK_CHARS) -> list[Chunk]:
    """Split one section into chunks of at most `limit` characters at line breaks."""
    chunks: list[Chunk] = []
    current = ""
    for line in text.splitlines():
        while len(line) > limit:  # a single very long line (e.g. a pasted log)
            if current:
                chunks.append(Chunk(heading=heading, text=current))
                current = ""
            chunks.append(Chunk(heading=heading, text=line[:limit]))
            line = line[limit:]
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(Chunk(heading=heading, text=current))
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(Chunk(heading=heading, text=current))
    return chunks


def incident_document(incident: IndexIncident) -> tuple[IndexedDocument, list[Chunk]]:
    document = IndexedDocument(
        key=f"incident:{incident.sys_id}",
        source="incident",
        kind="incident",
        sys_id=incident.sys_id,
        number=incident.number,
        title=incident.short_description,
        state=incident.state,
        resolution=incident.close_notes.strip(),
        updated_on=incident.updated_on,
    )
    chunks = chunk_text("Description", incident.description)
    if incident.close_notes.strip():
        chunks += chunk_text("Resolution", incident.close_notes)
    return document, chunks or [Chunk(heading="", text=incident.short_description)]


def _changed(versions: list[RecordVersion], indexed: dict[str, str]) -> list[str]:
    return [v.sys_id for v in versions if indexed.get(v.sys_id) != v.updated_on]


def sync_incidents(client: SyncClient, index: SearchBackend) -> SyncStats:
    versions = client.list_incident_versions()
    indexed = index.versions("incident")
    changed = _changed(versions, indexed)
    if changed:
        index.upsert_many([incident_document(i) for i in client.get_index_incidents(changed)])
    gone = set(indexed) - {v.sys_id for v in versions}
    index.delete("incident", gone)
    return SyncStats("incident", total=len(versions), fetched=len(changed), removed=len(gone))
