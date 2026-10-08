"""Experimental factual selection for #313/#321. Never called by production answer code."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
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


class Fusion(StrEnum):
    LEXICAL_FIRST = "lexical_first"
    WEIGHTED = "weighted"
    RRF = "rrf"


@dataclass(frozen=True)
class HybridConfig:
    """Experimental ranking policy. #321 will simplify the runtime strategy next."""

    calibration: SemanticCalibration | None = None
    fusion: Fusion = Fusion.LEXICAL_FIRST
    lexical_top_k: int = 20
    semantic_top_k: int = 20
    lexical_min: float = 12.0
    lexical_scale: float = 24.0
    min_score: float = 0.5
    # Disabled until an operator explicitly chooses a strong-hit cutoff from evaluation.
    skip_semantic_at_lexical: float | None = None
    semantic_weight: float = 0.5
    rrf_k: int = 60
    timeout_seconds: float = 30.0
    intent_hint: bool = False

    def __post_init__(self):
        if self.lexical_top_k <= 0 or self.semantic_top_k <= 0 or self.rrf_k <= 0:
            raise ValueError("top-K and RRF constant must be positive")
        if (
            not isfinite(self.lexical_min)
            or self.lexical_min <= 0
            or not isfinite(self.lexical_scale)
            or self.lexical_scale < self.lexical_min
        ):
            raise ValueError("invalid lexical thresholds")
        if not 0 < self.min_score <= 1 or not 0 <= self.semantic_weight <= 1:
            raise ValueError("invalid fusion thresholds")
        if not 0 < self.timeout_seconds <= 600:
            raise ValueError("invalid semantic deadline")
        if self.skip_semantic_at_lexical is not None and (
            not isfinite(self.skip_semantic_at_lexical)
            or self.skip_semantic_at_lexical < self.lexical_min
        ):
            raise ValueError("invalid lexical short-circuit cutoff")
        Fusion(self.fusion)


@dataclass(frozen=True)
class HybridResult:
    """Already-admitted factual rows plus content-free semantic diagnostics."""

    rows: tuple[RankedKnowledgeCandidate, ...]
    semantic_status: str
    cache_hits: int = 0
    cache_misses: int = 0
    elapsed_ms: float = 0.0


def eligible_candidates(
    candidates: Sequence[KnowledgeCandidate],
) -> list[KnowledgeCandidate]:
    """Filter only by factual retrieval eligibility and stable identity.

    Relation-pair correctness belongs to the exact relation lane; factual retrieval must
    not interpret RetrievalRequest.relation_pair as a generic candidate gate.
    """
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


def rank_hybrid(
    request: RetrievalRequest,
    candidates: Sequence[KnowledgeCandidate],
    config: HybridConfig,
    *,
    semantic_hits: Sequence[SemanticHit] | None = None,
) -> list[RankedKnowledgeCandidate]:
    """Fuse independent lexical and semantic evidence without cross-channel vetoes.

    This keeps the #313 experimental fusion strategies intact for one more cleanup step.
    Packing is no longer part of this function or HybridRetriever.
    """
    lexical = rank_lexical_candidates(request.retrieval_text, candidates)
    lex_scores = {row.order: float(row.score) for row in lexical}
    lexical_top = lexical[:config.lexical_top_k]
    lex_ranks = {row.order: i + 1 for i, row in enumerate(lexical_top)}

    semantic = list(semantic_hits or ())[:config.semantic_top_k]
    sem_ranks = {row.order: i + 1 for i, row in enumerate(semantic)}
    sem_scores = (
        {row.order: config.calibration.score(row.cosine) for row in semantic}
        if config.calibration is not None
        else {}
    )

    union = set(lex_ranks) | set(sem_ranks)
    if request.entities:
        query_entities = set(request.entities)
        union.update(
            i for i, row in enumerate(candidates)
            if row.entities and query_entities.issubset(row.entities)
        )

    profile = request.intent == RetrievalIntent.PROFILE
    ranked = []
    for order in union:
        lexical_score = lex_scores.get(order, 0.0)
        raw_lex = min(lexical_score / config.lexical_scale, 1.0)
        # Profile separation is handled in a later #321 step; preserve current behavior.
        lex_ok = lexical_score > 0 if profile else lexical_score >= config.lexical_min
        lex = max(raw_lex, config.min_score) if lex_ok else 0.0

        sem = sem_scores.get(order, 0.0)
        sem_ok = semantic_hits is not None and sem >= config.min_score
        if not lex_ok and not sem_ok:
            continue

        if semantic_hits is None:
            score = lex
        elif config.fusion == Fusion.WEIGHTED:
            if lex_ok and sem_ok:
                score = (
                    (1 - config.semantic_weight) * lex
                    + config.semantic_weight * sem
                )
            else:
                score = lex if lex_ok else sem
        elif config.fusion == Fusion.RRF:
            score = 0.0
            if lex_ok and order in lex_ranks:
                score += (config.rrf_k + 1) / (config.rrf_k + lex_ranks[order])
            if sem_ok and order in sem_ranks:
                score += (config.rrf_k + 1) / (config.rrf_k + sem_ranks[order])
        else:
            score = lex if lex_ok else sem

        if score < config.min_score:
            continue
        ranked.append(RankedKnowledgeCandidate(score, order, candidates[order]))

    if semantic_hits is not None and config.fusion == Fusion.LEXICAL_FIRST:
        ranked.sort(
            key=lambda row: (
                -(lex_scores.get(row.order, 0) > 0 if profile
                  else lex_scores.get(row.order, 0) >= config.lexical_min),
                -lex_scores.get(row.order, 0)
                if (lex_scores.get(row.order, 0) > 0 if profile
                    else lex_scores.get(row.order, 0) >= config.lexical_min)
                else -row.score,
                row.order,
            )
        )
    elif semantic_hits is None:
        ranked.sort(key=lambda row: (-lex_scores.get(row.order, 0), row.order))
    else:
        ranked.sort(key=lambda row: (-row.score, row.order))
    return ranked


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
        query = semantic_query(request, intent_hint=config.intent_hint)
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

        # Production invocation policy belongs to #316. Profile separation is the next
        # #321 cleanup step, so current lexical short-circuit behavior is preserved here.
        if request.intent == RetrievalIntent.PROFILE or strong:
            status = "lexical_short_circuit"
        elif self.index is not None and config.calibration is not None:
            if config.calibration.backend_key != self.index.backend.cache_key:
                status = "calibration_mismatch"
            else:
                try:
                    async with asyncio.timeout(config.timeout_seconds):
                        result = await self.index.search(
                            query, rows, top_k=config.semantic_top_k,
                        )
                    semantic_hits = result.hits
                    hits, misses = result.cache_hits, result.cache_misses
                    status = "available"
                except Exception:  # noqa: BLE001 - optional backend must never fail an answer
                    status = "failed"

        ranked = rank_hybrid(request, rows, config, semantic_hits=semantic_hits)
        return HybridResult(
            tuple(ranked),
            status,
            hits,
            misses,
            (perf_counter() - started) * 1000,
        )
