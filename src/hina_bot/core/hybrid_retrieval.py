"""Simple lexical-first factual retrieval with optional semantic recall."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from time import perf_counter

from .knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
    rank_lexical_candidates,
)
from .retrieval_v2 import RetrievalIntent, RetrievalRequest
from .semantic_retrieval import SemanticCalibration, SemanticHit, SemanticIndex, semantic_query


@dataclass(frozen=True)
class HybridConfig:
    """Runtime factual policy; experimental fusion variants live in calibration tooling."""

    calibration: SemanticCalibration | None = None
    lexical_min: float = 12.0
    semantic_min: float = 0.5
    skip_semantic_at_lexical: float | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self):
        if not isfinite(self.lexical_min) or self.lexical_min <= 0:
            raise ValueError("invalid lexical threshold")
        if not 0 < self.semantic_min <= 1:
            raise ValueError("invalid semantic threshold")
        if not 0 < self.timeout_seconds <= 600:
            raise ValueError("invalid semantic deadline")
        if self.skip_semantic_at_lexical is not None and (
            not isfinite(self.skip_semantic_at_lexical)
            or self.skip_semantic_at_lexical < self.lexical_min
        ):
            raise ValueError("invalid lexical short-circuit cutoff")


@dataclass(frozen=True)
class HybridResult:
    rows: tuple[RankedKnowledgeCandidate, ...]
    semantic_status: str
    cache_hits: int = 0
    cache_misses: int = 0
    elapsed_ms: float = 0.0
    lexical_selected: int = 0
    semantic_only_selected: int = 0
    embedding_prompt_tokens: int | None = 0
    embedding_requests: int = 0


def eligible_candidates(
    candidates: Sequence[KnowledgeCandidate],
) -> list[KnowledgeCandidate]:
    seen = set()
    result = []
    for row in candidates:
        if KnowledgeUsage.FACTUAL not in row.retrieval_usages:
            continue
        identity = (row.source, row.candidate_id)
        if identity in seen:
            raise ValueError("duplicate candidate identity")
        seen.add(identity)
        result.append(row)
    return result


def rank_factual(
    request: RetrievalRequest,
    candidates: Sequence[KnowledgeCandidate],
    config: HybridConfig,
    *,
    semantic_hits: Sequence[SemanticHit] | None = None,
) -> list[RankedKnowledgeCandidate]:
    """Preserve qualifying lexical order, then append semantic-only recall.

    Semantic search scores the full factual candidate set. There is no semantic top-K
    candidate gate and no weighted/RRF runtime policy. A row admitted lexically is never
    reordered or vetoed by semantic similarity.
    """
    lexical = rank_lexical_candidates(request.retrieval_text, candidates)
    lexical_rows = [
        row for row in lexical
        if row.score >= config.lexical_min
    ]
    lexical_orders = {row.order for row in lexical_rows}

    if semantic_hits is None or config.calibration is None:
        return lexical_rows

    semantic_only = []
    for hit in semantic_hits:
        if hit.order in lexical_orders:
            continue
        score = config.calibration.score(hit.cosine)
        if score < config.semantic_min:
            continue
        semantic_only.append(
            RankedKnowledgeCandidate(score, hit.order, candidates[hit.order])
        )
    # SemanticIndex returns cosine-descending hits, and calibration is monotonic.
    return [*lexical_rows, *semantic_only]


class HybridRetriever:
    def __init__(
        self, index: SemanticIndex | None = None, *, config: HybridConfig | None = None,
    ):
        self.index = index
        self.config = config or HybridConfig()

    async def retrieve(
        self,
        request: RetrievalRequest,
        candidates: Sequence[KnowledgeCandidate],
    ) -> HybridResult:
        started = perf_counter()
        config = self.config
        rows = eligible_candidates(candidates)

        # Profile retrieval has its own deterministic exact/lexical path. Keep accidental
        # calls safe and cheap rather than invoking semantic retrieval.
        if request.intent == RetrievalIntent.PROFILE:
            return HybridResult((), "not_applicable")
        query = semantic_query(request)
        if not query or not rows:
            return HybridResult((), "not_needed")

        lexical = rank_lexical_candidates(request.retrieval_text, rows)
        strong = (
            config.skip_semantic_at_lexical is not None
            and lexical
            and lexical[0].score >= config.skip_semantic_at_lexical
        )
        status = "unavailable"
        semantic_hits = None
        hits = misses = 0
        prompt_tokens: int | None = 0
        embedding_requests = 0

        if strong:
            status = "lexical_short_circuit"
        elif self.index is not None and config.calibration is not None:
            if config.calibration.backend_key != self.index.backend.cache_key:
                status = "calibration_mismatch"
            else:
                try:
                    async with asyncio.timeout(config.timeout_seconds):
                        # The corpus is intentionally small; score every factual candidate.
                        result = await self.index.search(query, rows, top_k=len(rows))
                    semantic_hits = result.hits
                    hits, misses = result.cache_hits, result.cache_misses
                    usage = result.candidate_usage + result.query_usage
                    prompt_tokens = usage.prompt_token_count
                    embedding_requests = usage.request_count
                    status = "available"
                except Exception:  # noqa: BLE001 - optional backend must never fail an answer
                    status = "failed"

        lexical_ranked = rank_factual(request, rows, config, semantic_hits=None)
        ranked = rank_factual(request, rows, config, semantic_hits=semantic_hits)
        return HybridResult(
            tuple(ranked),
            status,
            hits,
            misses,
            (perf_counter() - started) * 1000,
            len(lexical_ranked),
            max(0, len(ranked) - len(lexical_ranked)),
            prompt_tokens,
            embedding_requests,
        )
