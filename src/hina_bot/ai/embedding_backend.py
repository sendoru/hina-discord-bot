"""Opt-in Gemini Embedding 2 adapter; separate from answer-provider lifecycle."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from hina_bot.core.semantic_retrieval import EmbeddingResult, EmbeddingUsage, normalize


class EmbeddingError(RuntimeError):
    """Content-free error; do not surface provider response bodies or request headers."""


@dataclass(frozen=True)
class GeminiEmbeddingConfig:
    model: str = "gemini-embedding-2"
    dimensions: int = 768
    revision: str = "1"
    timeout_seconds: float = 15.0
    batch_size: int = 50

    def __post_init__(self):
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size must be an integer in 1..100")
        if not re.fullmatch(r"gemini-embedding-2(?:-[a-z0-9-]+)?", self.model):
            raise ValueError("this adapter supports Gemini Embedding 2 models only")
        if not 1 <= self.dimensions <= 3072 or not self.revision:
            raise ValueError("invalid dimensions or revision")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("invalid embedding timeout")


class GeminiEmbeddingBackend:
    def __init__(
        self, api_key: str, *, config: GeminiEmbeddingConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY is required")
        self.config = config or GeminiEmbeddingConfig()
        self._http = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta/",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            timeout=self.config.timeout_seconds, transport=transport,
        )

    @property
    def dimensions(self) -> int:
        return self.config.dimensions

    @property
    def cache_key(self) -> str:
        return (f"gemini:{self.config.model}:{self.config.revision}:"
                f"{self.dimensions}:search-document-v1")

    async def close(self):
        await self._http.aclose()

    def _content_request(self, text: str) -> dict:
        return {"model": f"models/{self.config.model}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self.dimensions}

    async def _request(self, method: str, payload: dict) -> dict:
        try:
            response = await self._http.post(f"models/{self.config.model}:{method}", json=payload)
        except httpx.HTTPError:
            raise EmbeddingError("Gemini embedding transport failure") from None
        if response.status_code != 200:
            raise EmbeddingError(f"Gemini embedding HTTP {response.status_code}")
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("invalid response")
            return data
        except (TypeError, ValueError):
            raise EmbeddingError("Gemini embedding invalid response") from None

    @staticmethod
    def _usage(data: dict) -> EmbeddingUsage:
        metadata = data.get("usageMetadata")
        count = metadata.get("promptTokenCount") if isinstance(metadata, dict) else None
        if count is not None and (type(count) is not int or count < 0):
            raise EmbeddingError("Gemini embedding invalid usage metadata")
        return EmbeddingUsage(count, 1)

    def _vectors(self, entries: object, expected: int):
        try:
            if not isinstance(entries, list) or len(entries) != expected:
                raise ValueError("embedding row count mismatch")
            return tuple(normalize(entry["values"], self.dimensions) for entry in entries)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise EmbeddingError("Gemini embedding invalid vectors") from None

    async def embed_query(self, text: str) -> EmbeddingResult:
        data = await self._request(
            "embedContent", self._content_request(f"task: search result | query: {text}"),
        )
        return EmbeddingResult(self._vectors([data.get("embedding")], 1), self._usage(data))

    async def embed_candidates(self, texts: Sequence[str]) -> EmbeddingResult:
        # Synchronous batches of independent requests, not multipart aggregated content.
        # API guarantees positional response order; validate cardinality and every vector
        # before returning ANY chunk to the index's atomic cache-fill transaction.
        vectors = []
        usage = EmbeddingUsage()
        for start in range(0, len(texts), self.config.batch_size):
            chunk = texts[start:start + self.config.batch_size]
            data = await self._request("batchEmbedContents", {"requests": [
                self._content_request(f"title: none | text: {text}") for text in chunk
            ]})
            vectors.extend(self._vectors(data.get("embeddings"), len(chunk)))
            usage += self._usage(data)
        return EmbeddingResult(tuple(vectors), usage)
