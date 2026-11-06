"""Text embeddings from Voyage AI (Anthropic's recommended embedding provider).

Called over its REST API with `requests`, like the other external services, so the
SDK and its dependencies aren't needed. The API key is read from VOYAGE_API_KEY and
never stored in settings, logged, or included in error messages.
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal, Protocol

import requests

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
# Voyage allows 1,000 texts and 320K tokens per request for voyage-4, but accounts
# without a payment method are limited to ~10K tokens and 3 requests per minute. Batches
# are capped by an estimated token count so ingestion works on either tier.
BATCH_SIZE = 128
BATCH_TOKENS = 8000
CHARS_PER_TOKEN = 4  # rough estimate for English; only used to size batches
MAX_ATTEMPTS = 6
RATE_LIMIT_WAIT_SECONDS = 21.0  # one request slot at 3 requests per minute
RETRY_STATUSES = {429, 500, 502, 503, 504}

InputType = Literal["document", "query"]


class EmbeddingError(RuntimeError):
    """Embedding failed. Messages never contain the API key."""


class Embedder(Protocol):
    model: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class VoyageEmbedder:
    def __init__(
        self,
        model: str,
        dimensions: int,
        api_key: str | None = None,
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
    ):
        self.model = model
        self.dimensions = dimensions
        self._api_key = api_key or os.environ.get("VOYAGE_API_KEY", "")
        self._session = session or requests.Session()
        self._timeout = timeout_seconds

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in _batches(texts):
            vectors += self._embed(batch, "document")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]

    def _embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        if not self._api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        # input_type matters: Voyage prepends a different instruction for queries and
        # documents, which improves retrieval over embedding both the same way.
        body: dict[str, Any] = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": self.dimensions,
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = self._session.post(
                    VOYAGE_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                if attempt == MAX_ATTEMPTS:
                    raise EmbeddingError(f"Voyage request failed: {type(exc).__name__}") from None
                time.sleep(2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(float(retry_after))
                elif resp.status_code == 429:
                    time.sleep(RATE_LIMIT_WAIT_SECONDS * attempt)
                else:
                    time.sleep(2**attempt)
                continue
            if resp.status_code != 200:
                raise EmbeddingError(f"Voyage returned HTTP {resp.status_code}")
            data = sorted(resp.json()["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in data]
            if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
                raise EmbeddingError("Voyage returned an unexpected number or size of vectors")
            return vectors
        raise EmbeddingError("Voyage request failed after retries")


def _batches(texts: list[str]) -> list[list[str]]:
    """Consecutive batches of at most BATCH_SIZE texts and ~BATCH_TOKENS tokens."""
    batches: list[list[str]] = []
    current: list[str] = []
    tokens = 0
    for text in texts:
        cost = len(text) // CHARS_PER_TOKEN + 1
        if current and (len(current) == BATCH_SIZE or tokens + cost > BATCH_TOKENS):
            batches.append(current)
            current, tokens = [], 0
        current.append(text)
        tokens += cost
    if current:
        batches.append(current)
    return batches
