"""Opt-in Gemini Embedding 2 adapter; separate from answer-provider lifecycle."""

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from hina_bot.core.semantic_retrieval import Vector, normalize


class EmbeddingError(RuntimeError):
    """Content-free error; do not surface provider response bodies or request headers."""


@dataclass(frozen=True)
class GeminiEmbeddingConfig:
    model: str = "gemini-embedding-2"
    dimensions: int = 768
    revision: str = "1"
    timeout_seconds: float = 15.0

    def __post_init__(self):
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

    async def _embed(self, text: str) -> Vector:
        try:
            response = await self._http.post(
                f"models/{self.config.model}:embedContent",
                json={"content": {"parts": [{"text": text}]},
                      "outputDimensionality": self.dimensions},
            )
        except httpx.HTTPError:
            raise EmbeddingError("Gemini embedding transport failure") from None
        if response.status_code != 200:
            raise EmbeddingError(f"Gemini embedding HTTP {response.status_code}")
        try:
            return normalize(response.json()["embedding"]["values"], self.dimensions)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise EmbeddingError("Gemini embedding invalid response") from None

    async def embed_query(self, text: str) -> Vector:
        return await self._embed(f"task: search result | query: {text}")

    async def embed_candidates(self, texts: Sequence[str]) -> list[Vector]:
        # Embedding 2 aggregates multi-part input. One independent request per document
        # avoids accidental corpus aggregation and limits concurrent quota pressure.
        result = []
        for start in range(0, len(texts), 4):
            batch = await asyncio.gather(*(
                self._embed(f"title: none | text: {text}") for text in texts[start:start + 4]
            ), return_exceptions=True)
            if any(isinstance(value, BaseException) for value in batch):
                raise EmbeddingError("Gemini candidate embedding failed")
            result.extend(batch)
        return result
