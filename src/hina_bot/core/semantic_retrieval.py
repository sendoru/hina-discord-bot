"""Provider-neutral, process-local semantic search. No production wiring or logging."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import fsum, isfinite, sqrt
from typing import Protocol

from .knowledge_retrieval import KnowledgeCandidate
from .retrieval_v2 import RetrievalRequest

Vector = tuple[float, ...]


class EmbeddingBackend(Protocol):
    @property
    def cache_key(self) -> str:
        """Include provider, model, revision, dimensions and input formatting version."""
        ...

    @property
    def dimensions(self) -> int: ...

    async def embed_query(self, text: str) -> Sequence[float]: ...

    async def embed_candidates(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


def normalize(values: Sequence[float], dimensions: int) -> Vector:
    if len(values) != dimensions or not values:
        raise ValueError("embedding dimension mismatch")
    vector = tuple(float(value) for value in values)
    if not all(isfinite(value) for value in vector):
        raise ValueError("non-finite embedding")
    norm = sqrt(fsum(value * value for value in vector))
    if not isfinite(norm) or norm == 0:
        raise ValueError("invalid embedding norm")
    return tuple(value / norm for value in vector)


def semantic_query(request: RetrievalRequest, *, intent_hint: bool = False) -> str:
    """Use the routing-normalized query; append an authorized anchor only if absent.

    No raw visible-message fallback, canonical-id prose, alias inference or new LLM call.
    Existing lexical expansion stays intact; the eval CLI compares intent hints separately.
    """
    text = " ".join(request.retrieval_text.split())
    anchor = " ".join(request.anchor_text.split())
    if not text:
        return ""
    if anchor and anchor not in text:
        text = f"{anchor}\n{text}"
    return f"{request.intent.value}: {text}" if intent_hint else text


@dataclass(frozen=True)
class SemanticHit:
    order: int
    cosine: float


@dataclass(frozen=True)
class SemanticSearch:
    hits: tuple[SemanticHit, ...]
    cache_hits: int
    cache_misses: int


@dataclass(frozen=True)
class SemanticCalibration:
    """Explicit experimental thresholds; there are deliberately no live defaults."""

    backend_key: str
    reject: float
    strong: float

    def __post_init__(self):
        if not self.backend_key or not -1 <= self.reject < self.strong <= 1:
            raise ValueError("expected backend key and -1 <= reject < strong <= 1")

    def score(self, cosine: float) -> float:
        if not isfinite(cosine):
            raise ValueError("non-finite cosine")
        return max(0.0, min(1.0, (cosine - self.reject) / (self.strong - self.reject)))


class SemanticIndex:
    """Bounded content-addressed LRU shared across static/runtime candidate snapshots.

    Cache only meaning text, never query vectors. Content/model changes miss automatically;
    changed metadata is returned from the fresh snapshot, never an old cached candidate.
    Removed rows cannot be searched. Old vectors age out; no disk/schema migration.
    One lock coalesces simultaneous cache fills, not the query API calls.
    """

    def __init__(self, backend: EmbeddingBackend, *, max_entries: int = 2048):
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.backend = backend
        self.max_entries = max_entries
        self._cache: OrderedDict[tuple[str, str], Vector] = OrderedDict()
        self._lock = asyncio.Lock()

    async def _vectors(self, candidates: Sequence[KnowledgeCandidate]):
        backend_key = self.backend.cache_key
        texts = [row.semantic_representation.strip() for row in candidates]
        keys = [(backend_key, sha256(text.encode()).hexdigest()) for text in texts]
        if any(not text for text in texts):
            raise ValueError("empty semantic representation")
        async with self._lock:
            missing = {key: text for key, text in zip(keys, texts)
                       if key not in self._cache}
            vectors = {key: self._cache[key] for key in keys if key in self._cache}
            hit_count = sum(key in self._cache for key in keys)
            if missing:
                embedded = await self.backend.embed_candidates(list(missing.values()))
                if len(embedded) != len(missing):
                    raise ValueError("embedding row count mismatch")
                fresh = {key: normalize(vector, self.backend.dimensions)
                         for key, vector in zip(missing, embedded)}
                vectors.update(fresh)
            if self.backend.cache_key != backend_key:
                raise ValueError("embedding backend changed during cache fill")
            # Commit only a completely validated batch, including when corpus > capacity.
            for key in keys:
                self._cache[key] = vectors[key]
                self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
            return [vectors[key] for key in keys], hit_count, len(missing)

    async def warm(self, candidates: Sequence[KnowledgeCandidate]) -> tuple[int, int]:
        """Optional prewarm outside turn latency budget; no query embedding."""
        _, hits, misses = await self._vectors(candidates)
        return hits, misses

    async def search(
        self, text: str, candidates: Sequence[KnowledgeCandidate], *, top_k: int,
    ) -> SemanticSearch:
        if not text.strip() or not candidates or top_k <= 0:
            return SemanticSearch((), 0, 0)
        key = self.backend.cache_key
        matrix, hits, misses = await self._vectors(candidates)
        query = normalize(await self.backend.embed_query(text), self.backend.dimensions)
        if self.backend.cache_key != key:
            raise ValueError("embedding backend changed during search")
        scored = [SemanticHit(i, max(-1.0, min(1.0, fsum(
            left * right for left, right in zip(vector, query)
        )))) for i, vector in enumerate(matrix)]
        scored.sort(key=lambda row: (-row.cosine, row.order))
        return SemanticSearch(tuple(scored[:top_k]), hits, misses)
