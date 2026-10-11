"""Provider-neutral, process-local semantic search. No production wiring or logging."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import fsum, isfinite, sqrt
from time import perf_counter
from typing import Protocol

from .knowledge_retrieval import KnowledgeCandidate
from .retrieval_v2 import RetrievalRequest

Vector = tuple[float, ...]


@dataclass(frozen=True)
class EmbeddingUsage:
    # None means the provider omitted usage, never a guessed token count.
    prompt_token_count: int | None = 0
    request_count: int = 0

    def __add__(self, other: EmbeddingUsage) -> EmbeddingUsage:
        tokens = (None if self.prompt_token_count is None or other.prompt_token_count is None
                  else self.prompt_token_count + other.prompt_token_count)
        return EmbeddingUsage(tokens, self.request_count + other.request_count)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: tuple[Vector, ...]
    usage: EmbeddingUsage = EmbeddingUsage()


class EmbeddingBackend(Protocol):
    @property
    def cache_key(self) -> str:
        """Include provider, model, revision, dimensions and input formatting version."""
        ...

    @property
    def dimensions(self) -> int: ...

    async def embed_query(self, text: str) -> EmbeddingResult: ...

    async def embed_candidates(self, texts: Sequence[str]) -> EmbeddingResult: ...


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
    """Independent conversational meaning, never lexical expansions or canonical ids.

    Deduplicate contained anchors after Unicode/case/punctuation/whitespace normalization.
    This intentionally does not guess equivalence for arbitrary paraphrases or negations.
    """
    text = " ".join(request.visible_text.split())
    anchor = " ".join(request.anchor_text.split())
    if not text:
        return ""

    def meaning_key(value: str) -> str:
        return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value).casefold())

    anchor_key = meaning_key(anchor)
    if anchor_key and anchor_key not in meaning_key(text):
        text = f"{anchor}\n{text}"
    return f"{request.intent.value}: {text}" if intent_hint else text


@dataclass(frozen=True)
class SemanticHit:
    order: int
    cosine: float


@dataclass(frozen=True)
class CandidateWarmup:
    cache_hits: int
    cache_misses: int
    usage: EmbeddingUsage = EmbeddingUsage()
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class SemanticSearch:
    hits: tuple[SemanticHit, ...]
    cache_hits: int
    cache_misses: int
    candidate_usage: EmbeddingUsage = EmbeddingUsage()
    candidate_warmup_ms: float = 0.0
    query_usage: EmbeddingUsage = EmbeddingUsage()
    query_embedding_ms: float = 0.0


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
        started = perf_counter()
        usage = EmbeddingUsage()
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
                if len(embedded.vectors) != len(missing):
                    raise ValueError("embedding row count mismatch")
                fresh = {key: normalize(vector, self.backend.dimensions)
                         for key, vector in zip(missing, embedded.vectors)}
                usage = embedded.usage
                vectors.update(fresh)
            if self.backend.cache_key != backend_key:
                raise ValueError("embedding backend changed during cache fill")
            # Commit only a completely validated batch, including when corpus > capacity.
            for key in keys:
                self._cache[key] = vectors[key]
                self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
            return [vectors[key] for key in keys], CandidateWarmup(
                hit_count, len(missing), usage, (perf_counter() - started) * 1000,
            )

    async def warm(self, candidates: Sequence[KnowledgeCandidate]) -> CandidateWarmup:
        """Optional prewarm outside turn latency budget; no query embedding."""
        _, result = await self._vectors(candidates)
        return result

    async def search(
        self, text: str, candidates: Sequence[KnowledgeCandidate], *, top_k: int,
    ) -> SemanticSearch:
        if not text.strip() or not candidates or top_k <= 0:
            return SemanticSearch((), 0, 0)
        key = self.backend.cache_key
        matrix, warmup = await self._vectors(candidates)
        started = perf_counter()
        embedded = await self.backend.embed_query(text)
        query_ms = (perf_counter() - started) * 1000
        if len(embedded.vectors) != 1:
            raise ValueError("query embedding row count mismatch")
        query = normalize(embedded.vectors[0], self.backend.dimensions)
        if self.backend.cache_key != key:
            raise ValueError("embedding backend changed during search")
        scored = [SemanticHit(i, max(-1.0, min(1.0, fsum(
            left * right for left, right in zip(vector, query)
        )))) for i, vector in enumerate(matrix)]
        scored.sort(key=lambda row: (-row.cosine, row.order))
        return SemanticSearch(
            tuple(scored[:top_k]), warmup.cache_hits, warmup.cache_misses,
            warmup.usage, warmup.elapsed_ms, embedded.usage, query_ms,
        )
