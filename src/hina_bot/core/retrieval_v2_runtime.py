"""End-to-end Retrieval v2 runtime composition and content-free comparison telemetry."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from time import perf_counter

from .ambient_retrieval import AmbientConfig, AmbientRetriever, AmbientSceneContext
from .evidence_sufficiency import EvidenceAssessment, assess_local_evidence
from .hybrid_retrieval import HybridConfig, HybridResult, HybridRetriever, eligible_candidates
from .knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
    rank_lexical_candidates,
)
from .lore import LoreIndex
from .profile_retrieval import rank_profile
from .relationship_grounding import RelationshipGrounder
from .retrieval_v2 import (
    BundleComposer,
    KnowledgeBundle,
    RetrievalRequest,
    UsageBudget,
    candidate_identity,
    reference_size,
)
from .semantic_retrieval import SemanticCalibration, SemanticIndex


@dataclass(frozen=True)
class RetrievalV2Budgets:
    max_items: int = 6
    max_chars: int = 3200
    relation_items: int = 4
    ambient_items: int = 2
    ambient_chars: int = 900
    reaction_items: int = 1

    def __post_init__(self) -> None:
        values = (
            self.max_items, self.max_chars, self.relation_items,
            self.ambient_items, self.ambient_chars, self.reaction_items,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("retrieval v2 budgets must be non-negative integers")


@dataclass(frozen=True)
class RetrievalV2Result:
    request: RetrievalRequest
    bundle: KnowledgeBundle
    evidence: EvidenceAssessment
    factual_status: str
    ambient_status: str
    factual_cache_hits: int = 0
    factual_cache_misses: int = 0
    ambient_cache_hits: int = 0
    ambient_cache_misses: int = 0
    factual_elapsed_ms: float = 0.0
    ambient_elapsed_ms: float = 0.0
    elapsed_ms: float = 0.0
    factual_invocation: str = "not_needed"
    factual_candidates: int = 0
    ambient_candidates: int = 0
    relation_candidates: int = 0
    reaction_candidates: int = 0
    lexical_selected: int = 0
    semantic_selected: int = 0
    semantic_rejected: int = 0
    ambient_rejected: int = 0
    embedding_prompt_tokens: int | None = 0
    embedding_requests: int = 0
    candidate_embedding_ms: float = 0.0
    query_embedding_ms: float = 0.0
    composer_duplicate_count: int = 0
    relation_chars: int = 0
    factual_chars: int = 0
    ambient_chars: int = 0
    reaction_chars: int = 0

    @property
    def selected_ids(self) -> tuple[str, ...]:
        return tuple(
            row.candidate.candidate_id
            for rows in (
                self.bundle.relations,
                self.bundle.facts,
                self.bundle.character_insights,
                self.bundle.reactions,
            )
            for row in rows
        )


def _factual_invocation(request: RetrievalRequest) -> str:
    if request.intent.value == "profile":
        return "profile_exact"
    if request.intent.value in {"fact", "relationship", "event", "relationship_or_event"}:
        return "information_intent"
    if request.entities:
        return "entity_context"
    if request.anchor_text and request.anchor_source:
        return "causal_anchor"
    return "not_needed"


def rank_reactions(
    request: RetrievalRequest,
    candidates: Sequence[KnowledgeCandidate],
    *,
    lexical_min: float = 12.0,
) -> tuple[RankedKnowledgeCandidate, ...]:
    """Small lexical reaction lane; unrelated phrases legitimately return zero rows."""
    eligible = [
        candidate
        for candidate in candidates
        if KnowledgeUsage.REACTION in candidate.retrieval_usages
    ]
    return tuple(
        row for row in rank_lexical_candidates(request.retrieval_text, eligible)
        if row.score >= lexical_min
    )


def bundle_references(bundle: KnowledgeBundle) -> tuple[dict, ...]:
    """Serialize v2 context while preserving lane semantics for prompt consumption."""
    result = []
    for usage, rows in (
        ("relation", bundle.relations),
        ("factual", bundle.facts),
        ("ambient", bundle.character_insights),
        ("reaction", bundle.reactions),
    ):
        for row in rows:
            item = dict(row.candidate.reference_item())
            item["retrieval_usage"] = usage
            result.append(item)
    return tuple(result)


def reference_ids(references: Iterable[dict]) -> tuple[str, ...]:
    return tuple(
        value
        for row in references
        if isinstance(row, dict)
        and isinstance((value := row.get("reference")), str)
        and value
    )


def _id_hashes(values: Iterable[str]) -> list[str]:
    return [
        hashlib.sha256(value.encode()).hexdigest()[:12]
        for value in values
    ]


def comparison_metrics(
    legacy_references: Iterable[dict],
    result: RetrievalV2Result,
) -> dict[str, object]:
    """Content-free stable-id comparison; never serialize query/reference text."""
    legacy = reference_ids(legacy_references)
    v2 = result.selected_ids
    legacy_set, v2_set = set(legacy), set(v2)
    overlap = len(legacy_set & v2_set)
    union = len(legacy_set | v2_set)
    return {
        "retrieval_legacy_selected": len(legacy),
        "retrieval_v2_selected": len(v2),
        "retrieval_legacy_id_hashes": _id_hashes(legacy),
        "retrieval_v2_id_hashes": _id_hashes(v2),
        "retrieval_overlap_count": overlap,
        "retrieval_overlap_rate": overlap / union if union else 1.0,
        "retrieval_legacy_zero": not legacy,
        "retrieval_v2_zero": not v2,
        "retrieval_relation_selected": len(result.bundle.relations),
        "retrieval_factual_selected": len(result.bundle.facts),
        "retrieval_ambient_selected": len(result.bundle.character_insights),
        "retrieval_reaction_selected": len(result.bundle.reactions),
        "retrieval_relation_hit": bool(result.bundle.relations),
        "retrieval_factual_status": result.factual_status,
        "retrieval_ambient_status": result.ambient_status,
        "retrieval_factual_invocation": result.factual_invocation,
        "retrieval_factual_candidates": result.factual_candidates,
        "retrieval_ambient_candidates": result.ambient_candidates,
        "retrieval_relation_candidates": result.relation_candidates,
        "retrieval_reaction_candidates": result.reaction_candidates,
        "retrieval_lexical_selected": result.lexical_selected,
        "retrieval_semantic_selected": result.semantic_selected,
        "retrieval_semantic_rejected": result.semantic_rejected,
        "retrieval_ambient_rejected": result.ambient_rejected,
        "retrieval_embedding_prompt_tokens": result.embedding_prompt_tokens,
        "retrieval_embedding_requests": result.embedding_requests,
        "retrieval_candidate_embedding_ms": round(result.candidate_embedding_ms),
        "retrieval_query_embedding_ms": round(result.query_embedding_ms),
        "retrieval_composer_duplicate_count": result.composer_duplicate_count,
        "retrieval_relation_chars": result.relation_chars,
        "retrieval_factual_chars": result.factual_chars,
        "retrieval_ambient_chars": result.ambient_chars,
        "retrieval_reaction_chars": result.reaction_chars,
        "retrieval_factual_cache_hits": result.factual_cache_hits,
        "retrieval_factual_cache_misses": result.factual_cache_misses,
        "retrieval_ambient_cache_hits": result.ambient_cache_hits,
        "retrieval_ambient_cache_misses": result.ambient_cache_misses,
        "retrieval_factual_elapsed_ms": round(result.factual_elapsed_ms),
        "retrieval_ambient_elapsed_ms": round(result.ambient_elapsed_ms),
        "retrieval_elapsed_ms": round(result.elapsed_ms),
        "retrieval_evidence_sufficient": result.evidence.sufficient,
        "retrieval_evidence_reason": result.evidence.reason,
        "retrieval_evidence_predicate": result.evidence.predicate or "",
        "retrieval_evidence_answer_state": result.evidence.answer_state,
    }


class RetrievalV2Engine:
    """Provider-neutral orchestration for v2 lanes; callers own rollout timing/mode."""

    def __init__(
        self,
        lore: LoreIndex,
        *,
        semantic_index: SemanticIndex | None = None,
        factual_calibration: SemanticCalibration | None = None,
        ambient_calibration: SemanticCalibration | None = None,
        budgets: RetrievalV2Budgets | None = None,
    ):
        self.lore = lore
        self.semantic_index = semantic_index
        self.budgets = budgets or RetrievalV2Budgets()
        self.factual = HybridRetriever(
            semantic_index,
            config=HybridConfig(calibration=factual_calibration),
        )
        self.ambient = AmbientRetriever(
            semantic_index,
            config=AmbientConfig(calibration=ambient_calibration),
        )
        supplemental = LoreIndex.load(str(files("hina_bot").joinpath(
            "data/relationship_grounding.jsonl",
        )))
        self._supplemental = tuple(supplemental.candidates(include_community=False))
        self._static = tuple(lore.candidates())
        self.relationships = RelationshipGrounder([*self._supplemental, *self._static])

    @property
    def semantic_ready(self) -> bool:
        return (
            self.semantic_index is not None
            and self.factual.config.calibration is not None
        )

    async def retrieve(
        self,
        request: RetrievalRequest,
        *,
        runtime_candidates: Sequence[KnowledgeCandidate] = (),
        scene: AmbientSceneContext | None = None,
        include_community: bool = True,
    ) -> RetrievalV2Result:
        started = perf_counter()
        static = [
            candidate for candidate in self._static
            if include_community or candidate.lane != "community_meme"
        ]
        candidates = [*runtime_candidates, *static]

        relation_rows = self.relationships.ground(request)
        invocation = _factual_invocation(request)
        factual_result = HybridResult((), "not_needed")
        if invocation == "profile_exact":
            factual_rows = rank_profile(request, candidates)
            factual_result = HybridResult(tuple(factual_rows), "profile_exact")
        elif invocation != "not_needed":
            factual_result = await self.factual.retrieve(request, candidates)

        ambient_result = None
        if scene is not None and request.intent.value == "conversation":
            ambient_result = await self.ambient.retrieve(request, scene, candidates)

        reaction_rows = rank_reactions(request, candidates)
        budget = self.budgets
        bundle = BundleComposer(
            {
                KnowledgeUsage.RELATION: UsageBudget(budget.relation_items, budget.max_chars),
                KnowledgeUsage.FACTUAL: UsageBudget(budget.max_items, budget.max_chars),
                KnowledgeUsage.AMBIENT: UsageBudget(
                    budget.ambient_items, min(budget.ambient_chars, budget.max_chars)
                ),
                KnowledgeUsage.REACTION: UsageBudget(budget.reaction_items, budget.max_chars),
            },
            max_total_chars=budget.max_chars,
            max_total_items=budget.max_items,
        ).compose({
            KnowledgeUsage.RELATION: relation_rows,
            KnowledgeUsage.FACTUAL: factual_result.rows,
            KnowledgeUsage.AMBIENT: (
                ambient_result.rows if ambient_result is not None else ()
            ),
            KnowledgeUsage.REACTION: reaction_rows,
        })
        evidence = assess_local_evidence(
            request,
            bundle,
            supporting_candidates=[*self._supplemental, *candidates],
        )
        admitted = (
            *relation_rows,
            *factual_result.rows,
            *(ambient_result.rows if ambient_result is not None else ()),
            *reaction_rows,
        )
        identities = [candidate_identity(row) for row in admitted]
        duplicate_count = len(identities) - len(set(identities))
        return RetrievalV2Result(
            request=request,
            bundle=bundle,
            evidence=evidence,
            factual_status=factual_result.semantic_status,
            ambient_status=(
                ambient_result.semantic_status if ambient_result is not None else "not_invoked"
            ),
            factual_cache_hits=factual_result.cache_hits,
            factual_cache_misses=factual_result.cache_misses,
            ambient_cache_hits=(
                ambient_result.cache_hits if ambient_result is not None else 0
            ),
            ambient_cache_misses=(
                ambient_result.cache_misses if ambient_result is not None else 0
            ),
            factual_elapsed_ms=factual_result.elapsed_ms,
            ambient_elapsed_ms=(
                ambient_result.elapsed_ms if ambient_result is not None else 0.0
            ),
            elapsed_ms=(perf_counter() - started) * 1000,
            factual_invocation=invocation,
            factual_candidates=len(eligible_candidates(candidates)),
            ambient_candidates=sum(
                KnowledgeUsage.AMBIENT in candidate.retrieval_usages
                for candidate in candidates
            ),
            relation_candidates=len(relation_rows),
            reaction_candidates=sum(
                KnowledgeUsage.REACTION in candidate.retrieval_usages
                for candidate in candidates
            ),
            lexical_selected=factual_result.lexical_admitted,
            semantic_selected=factual_result.semantic_admitted,
            semantic_rejected=factual_result.semantic_rejected,
            ambient_rejected=(
                ambient_result.semantic_rejected if ambient_result is not None else 0
            ),
            embedding_prompt_tokens=(
                None
                if (
                    factual_result.embedding_prompt_tokens is None
                    or (
                        ambient_result is not None
                        and ambient_result.embedding_prompt_tokens is None
                    )
                )
                else factual_result.embedding_prompt_tokens
                + (
                    ambient_result.embedding_prompt_tokens
                    if ambient_result is not None else 0
                )
            ),
            embedding_requests=(
                factual_result.embedding_requests
                + (ambient_result.embedding_requests if ambient_result is not None else 0)
            ),
            candidate_embedding_ms=(
                factual_result.candidate_embedding_ms
                + (
                    ambient_result.candidate_embedding_ms
                    if ambient_result is not None else 0.0
                )
            ),
            query_embedding_ms=(
                factual_result.query_embedding_ms
                + (
                    ambient_result.query_embedding_ms
                    if ambient_result is not None else 0.0
                )
            ),
            composer_duplicate_count=duplicate_count,
            relation_chars=sum(reference_size(row) for row in bundle.relations),
            factual_chars=sum(reference_size(row) for row in bundle.facts),
            ambient_chars=sum(
                reference_size(row) for row in bundle.character_insights
            ),
            reaction_chars=sum(reference_size(row) for row in bundle.reactions),
        )
