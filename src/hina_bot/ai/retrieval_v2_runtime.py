"""Production bridge for Retrieval v2 shadow/active rollout.

The runner owns no conversation history. Callers pass only already-authorized routing,
channel and relationship signals. Telemetry emitted from the result is content-free.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from time import perf_counter

from hina_bot.ai.embedding_backend import GeminiEmbeddingBackend, GeminiEmbeddingConfig
from hina_bot.ai.retrieval_request import build_resolved_retrieval_request
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.ai.structured_memory_context import structured_memory_context
from hina_bot.core.ambient_retrieval import (
    AmbientConfig,
    AmbientResult,
    AmbientRetriever,
    AmbientSceneContext,
)
from hina_bot.core.evidence_sufficiency import EvidenceAssessment, assess_local_evidence
from hina_bot.core.hybrid_retrieval import HybridConfig, HybridResult, HybridRetriever
from hina_bot.core.knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
    rank_lexical_candidates,
)
from hina_bot.core.lore import LoreIndex
from hina_bot.core.profile_retrieval import rank_profile
from hina_bot.core.relationship_grounding import RelationshipGrounder
from hina_bot.core.retrieval_v2 import (
    BundleComposer,
    KnowledgeBundle,
    RetrievalIntent,
    UsageBudget,
    reference_size,
)
from hina_bot.core.semantic_retrieval import SemanticCalibration, SemanticIndex

_RELATIONSHIP_LABELS = {
    "familiarity": "익숙함",
    "comfort": "편안함",
    "casualness": "편한 상호작용",
    "teasing_tolerance": "장난 수용",
    "support_openness": "도움 수용",
    "task_orientation": "업무 중심",
}


@dataclass(frozen=True)
class RetrievalV2Outcome:
    bundle: KnowledgeBundle
    evidence: EvidenceAssessment
    hybrid: HybridResult
    ambient: AmbientResult
    status: str
    elapsed_ms: float
    factual_candidates: int
    relation_candidates: int
    ambient_candidates: int
    reaction_candidates: int
    composer_input_rows: int

    @property
    def selected_count(self) -> int:
        return sum(len(rows) for rows in (
            self.bundle.facts,
            self.bundle.relations,
            self.bundle.character_insights,
            self.bundle.reactions,
        ))


def _stable_id(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _candidate_ref(row: RankedKnowledgeCandidate) -> str:
    return row.candidate.reference or row.candidate.candidate_id


def _relationship_signal(profile: dict) -> str:
    values = []
    for axis, label in _RELATIONSHIP_LABELS.items():
        value = profile.get(axis)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
            values.append(f"{label} {value:g}")
    return ", ".join(values)


def _recent_same_speaker(channel_context, user_id) -> tuple[str, ...]:
    owner = str(user_id)
    rows = []
    for row in channel_context or ():
        if row.get("role") != "user":
            continue
        author = str(row.get("author_user_id") or row.get("user_id") or "")
        content = row.get("content")
        if author == owner and isinstance(content, str) and content.strip():
            rows.append(content)
    return tuple(rows[-2:])


def _reaction_rows(
    request,
    candidates: tuple[KnowledgeCandidate, ...],
) -> tuple[RankedKnowledgeCandidate, ...]:
    # Reaction is intentionally lexical-only. A meaningful meme trigger must at least
    # reach the same strong lexical admission floor as factual retrieval.
    return tuple(
        row for row in rank_lexical_candidates(request.retrieval_text, candidates)
        if (
            KnowledgeUsage.REACTION in row.candidate.retrieval_usages
            and row.score >= 12
        )
    )


class RetrievalV2Runner:
    """Run the complete v2 chain behind a reversible rollout mode."""

    def __init__(self, settings, lore, runtime_lore, story_context):
        self.settings = settings
        self.lore = lore
        self.runtime_lore = runtime_lore
        self.story_context = story_context
        self._backend = None
        self._index = None
        self._calibration = None

        reject = settings.retrieval_v2_semantic_reject
        strong = settings.retrieval_v2_semantic_strong
        if (
            reject is not None
            and strong is not None
            and settings.gemini_api_key.strip()
        ):
            self._backend = GeminiEmbeddingBackend(
                settings.gemini_api_key,
                config=GeminiEmbeddingConfig(
                    model=settings.retrieval_v2_embedding_model,
                    dimensions=settings.retrieval_v2_embedding_dimensions,
                    revision=settings.retrieval_v2_embedding_revision,
                    timeout_seconds=min(15.0, settings.retrieval_v2_timeout_seconds),
                ),
            )
            self._index = SemanticIndex(self._backend)
            self._calibration = SemanticCalibration(
                self._backend.cache_key,
                reject,
                strong,
            )

    @property
    def semantic_ready(self) -> bool:
        return self._index is not None and self._calibration is not None

    async def close(self) -> None:
        if self._backend is not None:
            await self._backend.close()

    def _candidates(self) -> tuple[KnowledgeCandidate, ...]:
        static = self.lore.candidates(
            include_community=self.settings.community_lore,
        )
        try:
            runtime = [
                *self.runtime_lore.candidates(),
                *self.story_context.candidates(),
            ]
        except ValueError:
            runtime = []
        supplement = LoreIndex.load(str(files("hina_bot").joinpath(
            "data/relationship_grounding.jsonl",
        ))).candidates(include_community=False)
        rows = [*supplement, *static, *runtime]
        seen = set()
        result = []
        for row in rows:
            identity = (row.source, row.candidate_id)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(row)
        return tuple(result)

    def _composer(self) -> BundleComposer:
        lore_items = max(0, self.settings.lore_max_items)
        lore_chars = max(0, self.settings.lore_max_chars)
        return BundleComposer(
            {
                KnowledgeUsage.RELATION: UsageBudget(min(4, lore_items), min(1800, lore_chars)),
                KnowledgeUsage.FACTUAL: UsageBudget(lore_items, lore_chars),
                KnowledgeUsage.AMBIENT: UsageBudget(2, 900),
                KnowledgeUsage.REACTION: UsageBudget(1, 500),
            },
            max_total_chars=lore_chars + 900,
        )

    async def retrieve(
        self,
        routing: RoutingPlan,
        *,
        store,
        scope,
        channel_context=(),
        use_memory: bool = True,
    ) -> RetrievalV2Outcome:
        started = perf_counter()
        request = build_resolved_retrieval_request(
            routing,
            call_prefixes=getattr(self.settings, "call_prefixes", None),
        )
        candidates = self._candidates()
        factual_candidates = tuple(
            row for row in candidates
            if KnowledgeUsage.FACTUAL in row.retrieval_usages
        )
        relation_candidates = tuple(
            row for row in candidates
            if KnowledgeUsage.RELATION in row.retrieval_usages
        )
        ambient_candidates = tuple(
            row for row in candidates
            if KnowledgeUsage.AMBIENT in row.retrieval_usages
        )
        reaction_candidates = tuple(
            row for row in candidates
            if KnowledgeUsage.REACTION in row.retrieval_usages
        )

        if request.intent == RetrievalIntent.PROFILE:
            factual_rows = rank_profile(request, factual_candidates)
            hybrid = HybridResult(tuple(factual_rows), "profile_exact")
        else:
            hybrid = await HybridRetriever(
                self._index,
                config=HybridConfig(
                    calibration=self._calibration,
                    semantic_min=self.settings.retrieval_v2_semantic_min,
                    timeout_seconds=self.settings.retrieval_v2_timeout_seconds,
                ),
            ).retrieve(request, factual_candidates)
            factual_rows = hybrid.rows

        relations = RelationshipGrounder(relation_candidates).ground(request)

        relationship = structured_memory_context(
            store,
            scope,
            use_memory=use_memory,
            allow_cross_space=use_memory,
        )
        profile = (
            relationship["owner_relationship_profile"]
            if scope.guild_id is None
            else relationship["cross_space_relationship"]
        )
        scene = AmbientSceneContext(
            "character.hina",
            recent_same_speaker=_recent_same_speaker(channel_context, scope.user_id),
            relationship_signal=_relationship_signal(profile),
        )
        if request.intent == RetrievalIntent.CONVERSATION:
            ambient = await AmbientRetriever(
                self._index,
                config=AmbientConfig(
                    calibration=self._calibration,
                    semantic_min=self.settings.retrieval_v2_ambient_min,
                    timeout_seconds=self.settings.retrieval_v2_timeout_seconds,
                ),
            ).retrieve(request, scene, ambient_candidates)
        else:
            ambient = AmbientResult((), "not_applicable")

        reactions = _reaction_rows(request, reaction_candidates)
        admitted = {
            KnowledgeUsage.RELATION: relations,
            KnowledgeUsage.FACTUAL: factual_rows,
            KnowledgeUsage.AMBIENT: ambient.rows,
            KnowledgeUsage.REACTION: reactions,
        }
        bundle = self._composer().compose(admitted)
        evidence = assess_local_evidence(
            request,
            bundle,
            supporting_candidates=candidates,
        )
        input_count = sum(len(rows) for rows in admitted.values())
        return RetrievalV2Outcome(
            bundle,
            evidence,
            hybrid,
            ambient,
            "completed",
            (perf_counter() - started) * 1000,
            len(factual_candidates),
            len(relation_candidates),
            len(ambient_candidates),
            len(reaction_candidates),
            input_count,
        )

    async def retrieve_with_deadline(self, *args, **kwargs) -> RetrievalV2Outcome:
        async with asyncio.timeout(self.settings.retrieval_v2_timeout_seconds):
            return await self.retrieve(*args, **kwargs)

    @staticmethod
    def telemetry(
        outcome: RetrievalV2Outcome,
        legacy_references,
        *,
        mode: str,
        applied: bool,
    ) -> dict:
        legacy_ids = tuple(
            str(row.get("reference"))
            for row in legacy_references
            if isinstance(row, dict) and row.get("reference")
        )
        sections = {
            "facts": outcome.bundle.facts,
            "relations": outcome.bundle.relations,
            "ambient": outcome.bundle.character_insights,
            "reactions": outcome.bundle.reactions,
        }
        v2_ids = tuple(
            _candidate_ref(row)
            for rows in sections.values()
            for row in rows
        )
        overlap = len(set(legacy_ids) & set(v2_ids))
        section_chars = {
            name: sum(reference_size(row) for row in rows)
            for name, rows in sections.items()
        }
        prompt_tokens = outcome.hybrid.embedding_prompt_tokens
        ambient_tokens = outcome.ambient.embedding_prompt_tokens
        if prompt_tokens is None or ambient_tokens is None:
            total_tokens = None
        else:
            total_tokens = prompt_tokens + ambient_tokens
        return {
            "status": outcome.status,
            "retrieval_v2_mode": mode,
            "retrieval_v2_applied": applied,
            "retrieval_v2_semantic_ready": (
                outcome.hybrid.semantic_status not in {
                    "unavailable", "calibration_mismatch",
                }
            ),
            "retrieval_v2_legacy_selected": len(legacy_ids),
            "retrieval_v2_selected": len(v2_ids),
            "retrieval_v2_overlap": overlap,
            "retrieval_v2_overlap_rate": (
                overlap / max(1, len(set(legacy_ids) | set(v2_ids)))
            ),
            "retrieval_v2_legacy_ids": [_stable_id(value) for value in legacy_ids],
            "retrieval_v2_fact_ids": [
                _stable_id(_candidate_ref(row)) for row in sections["facts"]
            ],
            "retrieval_v2_relation_ids": [
                _stable_id(_candidate_ref(row)) for row in sections["relations"]
            ],
            "retrieval_v2_ambient_ids": [
                _stable_id(_candidate_ref(row)) for row in sections["ambient"]
            ],
            "retrieval_v2_reaction_ids": [
                _stable_id(_candidate_ref(row)) for row in sections["reactions"]
            ],
            "retrieval_v2_factual_candidates": outcome.factual_candidates,
            "retrieval_v2_relation_candidates": outcome.relation_candidates,
            "retrieval_v2_ambient_candidates": outcome.ambient_candidates,
            "retrieval_v2_reaction_candidates": outcome.reaction_candidates,
            "retrieval_v2_fact_selected": len(sections["facts"]),
            "retrieval_v2_relation_selected": len(sections["relations"]),
            "retrieval_v2_ambient_selected": len(sections["ambient"]),
            "retrieval_v2_reaction_selected": len(sections["reactions"]),
            "retrieval_v2_composer_deduped": max(
                0, outcome.composer_input_rows - outcome.selected_count
            ),
            "retrieval_v2_fact_chars": section_chars["facts"],
            "retrieval_v2_relation_chars": section_chars["relations"],
            "retrieval_v2_ambient_chars": section_chars["ambient"],
            "retrieval_v2_reaction_chars": section_chars["reactions"],
            "retrieval_v2_chars_total": sum(section_chars.values()),
            "retrieval_v2_hybrid_status": outcome.hybrid.semantic_status,
            "retrieval_v2_ambient_status": outcome.ambient.semantic_status,
            "retrieval_v2_lexical_admitted": outcome.hybrid.lexical_admitted,
            "retrieval_v2_semantic_admitted": outcome.hybrid.semantic_admitted,
            "retrieval_v2_semantic_rejected": (
                outcome.hybrid.semantic_rejected + outcome.ambient.semantic_rejected
            ),
            "retrieval_v2_cache_hits": (
                outcome.hybrid.cache_hits + outcome.ambient.cache_hits
            ),
            "retrieval_v2_cache_misses": (
                outcome.hybrid.cache_misses + outcome.ambient.cache_misses
            ),
            "retrieval_v2_embedding_requests": (
                outcome.hybrid.embedding_requests + outcome.ambient.embedding_requests
            ),
            "retrieval_v2_embedding_prompt_tokens": total_tokens,
            "retrieval_v2_embedding_ms": round(
                outcome.hybrid.candidate_embedding_ms
                + outcome.hybrid.query_embedding_ms
                + outcome.ambient.candidate_embedding_ms
                + outcome.ambient.query_embedding_ms,
                3,
            ),
            "retrieval_v2_elapsed_ms": round(outcome.elapsed_ms, 3),
            "retrieval_v2_evidence_sufficient": outcome.evidence.sufficient,
            "retrieval_v2_evidence_reason": outcome.evidence.reason,
            "retrieval_v2_evidence_predicate": outcome.evidence.predicate or "",
            "retrieval_v2_evidence_state": outcome.evidence.answer_state,
        }


def v2_references(bundle: KnowledgeBundle) -> tuple[dict, ...]:
    """Flatten factual answer evidence only; ambient/reaction stay separate sections."""
    return tuple(
        row.candidate.reference_item()
        for row in (*bundle.relations, *bundle.facts)
    )


def v2_context_sections(bundle: KnowledgeBundle) -> dict[str, tuple[dict, ...]]:
    sections = bundle.context_sections()
    return {
        "character_insights": tuple(sections["character_insights"]),
        "reaction_guides": tuple(sections["reactions"]),
    }
