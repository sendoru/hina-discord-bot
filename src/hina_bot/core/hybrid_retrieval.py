"""Experimental factual selection for #313. Never called by production answer code."""

from __future__ import annotations

import asyncio
import json
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
from .retrieval_v2 import KnowledgeBundle, RetrievalIntent, RetrievalRequest, UsageBudget
from .semantic_retrieval import SemanticCalibration, SemanticHit, SemanticIndex, semantic_query


class Fusion(StrEnum):
    LEXICAL_FIRST = "lexical_first"
    WEIGHTED = "weighted"
    RRF = "rrf"


@dataclass(frozen=True)
class HybridConfig:
    """All numeric defaults are provisional evaluation settings, not calibrated policy.

    Missing calibration disables semantic calls. No Settings/env/production enable switch
    is introduced here. A caller must explicitly construct the experimental v2 retriever.
    """

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
        if (not isfinite(self.lexical_min) or self.lexical_min <= 0
                or not isfinite(self.lexical_scale) or self.lexical_scale < self.lexical_min):
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
    bundle: KnowledgeBundle
    semantic_status: str
    cache_hits: int = 0
    cache_misses: int = 0
    elapsed_ms: float = 0.0


def eligible_candidates(
    request: RetrievalRequest, candidates: Sequence[KnowledgeCandidate],
) -> list[KnowledgeCandidate]:
    required = set(request.required_entities)
    seen = set()
    result = []
    for row in candidates:
        # Apply the supplied constraint before ALL channels, including lexical fallback.
        if KnowledgeUsage.FACTUAL not in row.retrieval_usages:
            continue
        if not required.issubset(row.entities):
            continue
        identity = (row.source, row.candidate_id)
        if identity in seen:
            raise ValueError("duplicate candidate identity")
        seen.add(identity)
        result.append(row)
    return result


def rank_hybrid(
    request: RetrievalRequest, candidates: Sequence[KnowledgeCandidate],
    config: HybridConfig, *, semantic_hits: Sequence[SemanticHit] | None = None,
) -> list[RankedKnowledgeCandidate]:
    """Pure fusion used by both the retriever and calibration CLI.

    Candidate sequence must already satisfy usage/entity constraints. Entity membership
    nominates a candidate, never proves relevance. Source is never a ranking tier.
    RRF uses absolute admission thresholds too: first place alone cannot imply relevance.
    """
    lexical = rank_lexical_candidates(request.retrieval_text, candidates)
    lex_scores = {row.order: float(row.score) for row in lexical}
    lex_ranks = {row.order: i + 1 for i, row in enumerate(lexical[:config.lexical_top_k])}
    semantic = list(semantic_hits or ())[:config.semantic_top_k]
    sem_ranks = {row.order: i + 1 for i, row in enumerate(semantic)}
    sem_scores = {
        row.order: config.calibration.score(row.cosine) for row in semantic
    } if config.calibration is not None else {}
    union = set(lex_ranks) | set(sem_ranks)
    if request.entities:
        union.update(i for i, row in enumerate(candidates)
                     if set(request.entities).issubset(row.entities))
    ranked = []
    for order in union:
        lexical_score = lex_scores.get(order, 0.0)
        lex = min(lexical_score / config.lexical_scale, 1.0)
        sem = sem_scores.get(order, 0.0)
        lex_ok = lexical_score >= config.lexical_min
        # Semantic rejection can suppress weak lexical overlap in the experimental path.
        # Exact/profile bypass happens before this function; backend failure has no veto.
        if semantic_hits is not None:
            lex_ok = lex_ok and sem > 0
        if not lex_ok and sem < config.min_score:
            continue
        if semantic_hits is None:
            score = lex
        elif config.fusion == Fusion.WEIGHTED:
            score = (1 - config.semantic_weight) * lex + config.semantic_weight * sem
        elif config.fusion == Fusion.RRF:
            score = sum((config.rrf_k + 1) / (config.rrf_k + ranks[order])
                        for ranks in (lex_ranks, sem_ranks) if order in ranks)
        else:
            # Strong-enough lexical evidence retains order; semantic fills recall gaps.
            score = lex if lex_ok else sem
        if score < config.min_score:
            continue
        ranked.append(RankedKnowledgeCandidate(score, order, candidates[order]))
    if semantic_hits is not None and config.fusion == Fusion.LEXICAL_FIRST:
        ranked.sort(key=lambda row: (
            -(lex_scores.get(row.order, 0) >= config.lexical_min
              and sem_scores.get(row.order, 0) > 0),
            -lex_scores.get(row.order, 0) if (lex_scores.get(row.order, 0) >= config.lexical_min
                                             and sem_scores.get(row.order, 0) > 0) else -row.score,
            row.order,
        ))
    elif semantic_hits is None:
        ranked.sort(key=lambda row: (-lex_scores.get(row.order, 0), row.order))
    else:
        ranked.sort(key=lambda row: (-row.score, row.order))
    return ranked


def pack_facts(ranked: Sequence[RankedKnowledgeCandidate], budget: UsageBudget) -> KnowledgeBundle:
    rows, used = [], 0
    if budget.max_items <= 0 or budget.max_chars <= 0:
        return KnowledgeBundle()
    for row in ranked:
        if row.score <= budget.min_score:
            continue
        size = len(json.dumps(row.candidate.reference_item(), ensure_ascii=False))
        if used + size > budget.max_chars:
            continue
        rows.append(row)
        used += size
        if len(rows) >= budget.max_items:
            break
    return KnowledgeBundle(facts=tuple(rows))


class HybridRetriever:
    def __init__(self, index: SemanticIndex | None = None, *, config: HybridConfig | None = None):
        self.index = index
        self.config = config or HybridConfig()

    async def retrieve(
        self, request: RetrievalRequest, candidates: Sequence[KnowledgeCandidate], *,
        budget: UsageBudget,
    ) -> HybridResult:
        started = perf_counter()
        config = self.config
        rows = eligible_candidates(request, candidates)
        query = semantic_query(request, intent_hint=config.intent_hint)
        if (request.intent == RetrievalIntent.CONVERSATION or not query or not rows
                or budget.max_items <= 0 or budget.max_chars <= 0):
            return HybridResult(KnowledgeBundle(), "not_needed")
        lexical = rank_lexical_candidates(request.retrieval_text, rows)
        strong = (config.skip_semantic_at_lexical is not None and lexical
                  and lexical[0].score >= config.skip_semantic_at_lexical)
        status = "unavailable"
        semantic_hits = None
        hits = misses = 0
        if request.intent == RetrievalIntent.PROFILE or strong:
            status = "lexical_short_circuit"
        elif self.index is not None and config.calibration is not None:
            if config.calibration.backend_key != self.index.backend.cache_key:
                status = "calibration_mismatch"
            else:
                try:
                    async with asyncio.timeout(config.timeout_seconds):
                        result = await self.index.search(query, rows, top_k=config.semantic_top_k)
                    semantic_hits = result.hits
                    hits, misses = result.cache_hits, result.cache_misses
                    status = "available"
                except Exception:  # noqa: BLE001 - optional backend must never fail an answer
                    # No exception text/request content is logged. Cancellation still propagates.
                    status = "failed"
        ranked = rank_hybrid(request, rows, config, semantic_hits=semantic_hits)
        return HybridResult(pack_facts(ranked, budget), status, hits, misses,
                            (perf_counter() - started) * 1000)
