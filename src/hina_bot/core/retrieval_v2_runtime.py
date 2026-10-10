"""End-to-end Retrieval v2 composition used by shadow and active rollout."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter

from .ambient_retrieval import (
    AmbientConfig,
    AmbientResult,
    AmbientRetriever,
    AmbientSceneContext,
    eligible_ambient_candidates,
)
from .evidence_sufficiency import EvidenceAssessment, assess_local_evidence
from .hybrid_retrieval import (
    HybridConfig,
    HybridResult,
    HybridRetriever,
    eligible_candidates,
)
from .knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
    rank_lexical_candidates,
)
from .profile_retrieval import rank_profile
from .relationship_grounding import RelationshipGrounder
from .retrieval_v2 import (
    BundleComposer,
    KnowledgeBundle,
    RetrievalIntent,
    RetrievalRequest,
    UsageBudget,
    candidate_identity,
    reference_size,
)
from .semantic_retrieval import SemanticCalibration, SemanticIndex

_REACTION_MIN_LEXICAL = 12.0


@dataclass(frozen=True)
class RetrievalV2Budgets:
    relation: UsageBudget
    factual: UsageBudget
    ambient: UsageBudget
    reaction: UsageBudget
    max_total_chars: int

    @classmethod
    def from_legacy(cls, max_items: int, max_chars: int) -> "RetrievalV2Budgets":
        items = max(0, int(max_items))
        chars = max(0, int(max_chars))
        return cls(
            relation=UsageBudget(min(4, items), chars),
            factual=UsageBudget(items, chars),
            ambient=UsageBudget(min(2, items), min(900, chars)),
            reaction=UsageBudget(min(1, items), min(500, chars)),
            max_total_chars=chars,
        )

    def mapping(self) -> Mapping[KnowledgeUsage, UsageBudget]:
        return {
            KnowledgeUsage.RELATION: self.relation,
            KnowledgeUsage.FACTUAL: self.factual,
            KnowledgeUsage.AMBIENT: self.ambient,
            KnowledgeUsage.REACTION: self.reaction,
        }


@dataclass(frozen=True)
class RetrievalV2Run:
    bundle: KnowledgeBundle
    evidence: EvidenceAssessment
    status: str
    calibration_status: str
    factual_invocation: str
    factual_semantic_status: str
    ambient_semantic_status: str
    candidate_factual: int
    candidate_relation: int
    candidate_ambient: int
    candidate_reaction: int
    selected_factual: int
    selected_relation: int
    selected_ambient: int
    selected_reaction: int
    factual_lexical_selected: int
    factual_semantic_only_selected: int
    semantic_cache_hits: int
    semantic_cache_misses: int
    embedding_prompt_tokens: int | None
    embedding_requests: int
    composer_dedup_candidates: int
    elapsed_ms: float

    @property
    def selected_ids(self) -> tuple[str, ...]:
        rows = (
            *self.bundle.relations,
            *self.bundle.facts,
            *self.bundle.character_insights,
            *self.bundle.reactions,
        )
        return tuple(row.candidate.candidate_id for row in rows)

    @property
    def zero_result(self) -> bool:
        return not self.selected_ids

    def section_chars(self) -> dict[str, int]:
        return {
            "relation": sum(reference_size(row) for row in self.bundle.relations),
            "factual": sum(reference_size(row) for row in self.bundle.facts),
            "ambient": sum(reference_size(row) for row in self.bundle.character_insights),
            "reaction": sum(reference_size(row) for row in self.bundle.reactions),
        }


def _sum_optional(first: int | None, second: int | None) -> int | None:
    if first is None or second is None:
        return None
    return first + second


def _semantic_invocation(
    request: RetrievalRequest,
    *,
    calibration: SemanticCalibration | None,
    index: SemanticIndex | None,
) -> str:
    if index is None:
        return "embedding_backend_unavailable"
    if calibration is None:
        return "calibration_disabled"
    if request.intent == RetrievalIntent.PROFILE:
        return "profile_exact"
    if request.intent in {
        RetrievalIntent.FACT,
        RetrievalIntent.RELATIONSHIP,
        RetrievalIntent.EVENT,
        RetrievalIntent.RELATIONSHIP_OR_EVENT,
    }:
        return "semantic_enabled"
    if request.entities:
        return "semantic_entity_context"
    if request.anchor_text:
        return "semantic_anchor_context"
    return "conversation_no_factual_signal"


def rank_reactions(
    request: RetrievalRequest,
    candidates: Sequence[KnowledgeCandidate],
) -> tuple[RankedKnowledgeCandidate, ...]:
    rows = [
        row
        for row in candidates
        if KnowledgeUsage.REACTION in row.retrieval_usages
    ]
    return tuple(
        ranked
        for ranked in rank_lexical_candidates(request.retrieval_text, rows)
        if ranked.score >= _REACTION_MIN_LEXICAL
    )


def _duplicate_candidate_count(
    rows: Mapping[KnowledgeUsage, Sequence[RankedKnowledgeCandidate]],
) -> int:
    seen = set()
    duplicates = 0
    for usage in (
        KnowledgeUsage.RELATION,
        KnowledgeUsage.FACTUAL,
        KnowledgeUsage.AMBIENT,
        KnowledgeUsage.REACTION,
    ):
        for row in rows.get(usage, ()):
            identity = candidate_identity(row)
            if identity in seen:
                duplicates += 1
            else:
                seen.add(identity)
    return duplicates


async def retrieve_v2(
    request: RetrievalRequest,
    candidates: Sequence[KnowledgeCandidate],
    *,
    grounder: RelationshipGrounder,
    semantic_index: SemanticIndex | None,
    calibration: SemanticCalibration | None,
    calibration_status: str,
    scene: AmbientSceneContext,
    budgets: RetrievalV2Budgets,
    ambient_min_score: float = 0.75,
) -> RetrievalV2Run:
    """Run all v2 lanes and compose one typed bundle.

    This function has no rollout-mode knowledge. Shadow/active orchestration decides whether
    to await it and whether the resulting bundle becomes answer context.
    """
    started = perf_counter()
    factual_candidates = eligible_candidates(candidates)
    relation_rows = grounder.ground(request)
    ambient_candidates = eligible_ambient_candidates(scene, candidates)
    reaction_candidates = [
        row for row in candidates
        if KnowledgeUsage.REACTION in row.retrieval_usages
    ]

    invocation = _semantic_invocation(
        request,
        calibration=calibration,
        index=semantic_index,
    )
    factual_result = HybridResult((), "not_applicable")
    if request.intent == RetrievalIntent.PROFILE:
        factual_rows = rank_profile(request, candidates)
    else:
        semantic_allowed = invocation in {
            "semantic_enabled",
            "semantic_entity_context",
            "semantic_anchor_context",
        }
        factual_result = await HybridRetriever(
            semantic_index,
            config=HybridConfig(
                calibration=calibration if semantic_allowed else None,
            ),
        ).retrieve(request, candidates)
        factual_rows = factual_result.rows

    ambient_result = AmbientResult((), "not_applicable")
    ambient_rows: tuple[RankedKnowledgeCandidate, ...] = ()
    if request.intent == RetrievalIntent.CONVERSATION:
        ambient_result = await AmbientRetriever(
            semantic_index,
            config=AmbientConfig(
                calibration=calibration,
                semantic_min=ambient_min_score,
            ),
        ).retrieve(request, scene, candidates)
        ambient_rows = ambient_result.rows

    reaction_rows = rank_reactions(request, candidates)
    lane_rows = {
        KnowledgeUsage.RELATION: relation_rows,
        KnowledgeUsage.FACTUAL: factual_rows,
        KnowledgeUsage.AMBIENT: ambient_rows,
        KnowledgeUsage.REACTION: reaction_rows,
    }
    bundle = BundleComposer(
        budgets.mapping(),
        max_total_chars=budgets.max_total_chars,
    ).compose(lane_rows)
    evidence = assess_local_evidence(
        request,
        bundle,
        supporting_candidates=candidates,
    )

    prompt_tokens = _sum_optional(
        factual_result.embedding_prompt_tokens,
        ambient_result.embedding_prompt_tokens,
    )
    return RetrievalV2Run(
        bundle=bundle,
        evidence=evidence,
        status="completed",
        calibration_status=calibration_status,
        factual_invocation=invocation,
        factual_semantic_status=factual_result.semantic_status,
        ambient_semantic_status=ambient_result.semantic_status,
        candidate_factual=len(factual_candidates),
        candidate_relation=len(relation_rows),
        candidate_ambient=len(ambient_candidates),
        candidate_reaction=len(reaction_candidates),
        selected_factual=len(bundle.facts),
        selected_relation=len(bundle.relations),
        selected_ambient=len(bundle.character_insights),
        selected_reaction=len(bundle.reactions),
        factual_lexical_selected=factual_result.lexical_selected,
        factual_semantic_only_selected=factual_result.semantic_only_selected,
        semantic_cache_hits=(
            factual_result.cache_hits + ambient_result.cache_hits
        ),
        semantic_cache_misses=(
            factual_result.cache_misses + ambient_result.cache_misses
        ),
        embedding_prompt_tokens=prompt_tokens,
        embedding_requests=(
            factual_result.embedding_requests + ambient_result.embedding_requests
        ),
        composer_dedup_candidates=_duplicate_candidate_count(lane_rows),
        elapsed_ms=(perf_counter() - started) * 1000,
    )
