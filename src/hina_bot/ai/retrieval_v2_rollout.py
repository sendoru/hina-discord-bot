"""Production orchestration for Retrieval v2 shadow/active rollout."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from importlib.resources import files

from hina_bot.core.ambient_retrieval import AmbientSceneContext
from .embedding_backend import GeminiEmbeddingBackend, GeminiEmbeddingConfig
from hina_bot.core.lore import LoreIndex
from hina_bot.core.relationship_grounding import RelationshipGrounder
from hina_bot.core.relationship_profile import (
    aggregate_owner_relationship_evidence,
    aggregate_relationship_evidence,
)
from hina_bot.core.retrieval_v2_runtime import (
    RetrievalV2Budgets,
    RetrievalV2Run,
    retrieve_v2,
)
from hina_bot.core.semantic_retrieval import SemanticCalibration, SemanticIndex

from .retrieval_request import build_resolved_retrieval_request
from .routing_plan import RoutingPlan

_RELATIONSHIP_LABELS = {
    "familiarity": "익숙함",
    "comfort": "편안함",
    "casualness": "편한 상호작용",
    "teasing_tolerance": "장난 수용",
    "support_openness": "도움 수용",
    "task_orientation": "업무 중심",
}


@dataclass(frozen=True)
class PreparedRetrievalV2:
    request: object
    candidates: tuple
    scene: AmbientSceneContext
    legacy_ids: tuple[str, ...]


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _relationship_signal(store, scope, *, use_memory: bool) -> str:
    if not use_memory:
        return ""
    reader = getattr(store, "memory_items", None)
    if not callable(reader):
        return ""
    items = reader(scope.user_id)
    profile = (
        aggregate_owner_relationship_evidence(items, scope)
        if scope.guild_id is None
        else aggregate_relationship_evidence(items, scope)
    )
    values = [
        f"{_RELATIONSHIP_LABELS[axis]} {profile[axis]}/4"
        for axis in _RELATIONSHIP_LABELS
        if profile.get(axis)
    ]
    return ", ".join(values)


def _recent_same_speaker(store, scope, channel_context, *, use_memory: bool) -> tuple[str, ...]:
    if not use_memory:
        return ()
    current = str(scope.user_id)
    found = []
    for row in reversed(channel_context or ()):
        if row.get("role") != "user":
            continue
        author = str(row.get("author_user_id") or row.get("user_id") or "")
        content = row.get("content")
        if author == current and isinstance(content, str) and content.strip():
            found.append(content)
            if len(found) >= 2:
                break
    if scope.guild_id is None and len(found) < 2 and hasattr(store, "history"):
        for turn in reversed(store.history(scope)):
            content = turn.get("content")
            if isinstance(content, str) and content.strip() and content not in found:
                found.append(content)
                if len(found) >= 2:
                    break
    return tuple(reversed(found))


class RetrievalV2Coordinator:
    """Own the embedding backend/cache and build safe shadow/active snapshots."""

    def __init__(self, settings, usage, lore, runtime_lore, story_context):
        self.settings = settings
        self.usage = usage
        self.lore = lore
        self.runtime_lore = runtime_lore
        self.story_context = story_context
        self._backend = None
        self._index = None
        key = str(getattr(settings, "gemini_api_key", "") or "").strip()
        if key:
            self._backend = GeminiEmbeddingBackend(
                key,
                config=GeminiEmbeddingConfig(),
            )
            self._index = SemanticIndex(self._backend)
        self._supplement = LoreIndex.load(str(files("hina_bot").joinpath(
            "data/relationship_grounding.jsonl",
        )))
        self._grounder = RelationshipGrounder([
            *self._supplement.candidates(include_community=False),
            *self.lore.candidates(include_community=False),
        ])

    async def close(self) -> None:
        if self._backend is not None:
            await self._backend.close()

    def mode(self) -> str:
        return str(getattr(self.settings, "retrieval_v2_mode", "off") or "off")

    def calibration(self) -> tuple[SemanticCalibration | None, str]:
        if self._index is None:
            return None, "embedding_backend_unavailable"
        reject = float(getattr(self.settings, "retrieval_v2_semantic_reject", 0.0))
        strong = float(getattr(self.settings, "retrieval_v2_semantic_strong", 0.0))
        if reject == 0.0 and strong == 0.0:
            return None, "disabled_unmeasured"
        if reject >= strong:
            return None, "invalid_thresholds"
        try:
            return (
                SemanticCalibration(self._index.backend.cache_key, reject, strong),
                "configured",
            )
        except ValueError:
            return None, "invalid_thresholds"

    def _candidates(self) -> tuple:
        dynamic = [
            *self.runtime_lore.candidates(),
            *self.story_context.candidates(),
        ]
        static = self.lore.candidates(
            include_community=bool(getattr(self.settings, "community_lore", True)),
        )
        supplemental = self._supplement.candidates(include_community=False)
        return tuple([*dynamic, *supplemental, *static])

    @staticmethod
    def _legacy_ids(references, candidates) -> tuple[str, ...]:
        by_reference = {
            candidate.reference or candidate.candidate_id: candidate.candidate_id
            for candidate in candidates
        }
        result = []
        for row in references or ():
            reference = row.get("reference") if isinstance(row, dict) else None
            if isinstance(reference, str) and reference in by_reference:
                result.append(by_reference[reference])
        return tuple(dict.fromkeys(result))

    def prepare(
        self,
        routing: RoutingPlan,
        legacy_references,
        *,
        store,
        scope,
        channel_context,
        use_memory: bool,
        call_prefixes,
    ) -> PreparedRetrievalV2:
        candidates = self._candidates()
        request = build_resolved_retrieval_request(
            routing,
            call_prefixes=call_prefixes,
        )
        scene = AmbientSceneContext(
            rp_entity="character.hina",
            recent_same_speaker=_recent_same_speaker(
                store,
                scope,
                channel_context,
                use_memory=use_memory,
            ),
            relationship_signal=_relationship_signal(
                store,
                scope,
                use_memory=use_memory,
            ),
        )
        return PreparedRetrievalV2(
            request=request,
            candidates=candidates,
            scene=scene,
            legacy_ids=self._legacy_ids(legacy_references, candidates),
        )

    async def run(self, prepared: PreparedRetrievalV2) -> RetrievalV2Run:
        calibration, calibration_status = self.calibration()
        budgets = RetrievalV2Budgets.from_legacy(
            int(getattr(self.settings, "lore_max_items", 6)),
            int(getattr(self.settings, "lore_max_chars", 3200)),
        )
        return await retrieve_v2(
            prepared.request,
            prepared.candidates,
            grounder=self._grounder,
            semantic_index=self._index,
            calibration=calibration,
            calibration_status=calibration_status,
            scene=prepared.scene,
            budgets=budgets,
            ambient_min_score=float(
                getattr(self.settings, "retrieval_v2_ambient_min_score", 0.75)
            ),
        )

    def telemetry(
        self,
        prepared: PreparedRetrievalV2,
        run: RetrievalV2Run,
        *,
        mode: str,
        status: str = "completed",
        fallback_reason: str = "",
    ) -> dict:
        legacy = set(prepared.legacy_ids)
        selected = set(run.selected_ids)
        overlap = legacy & selected
        union = legacy | selected
        chars = run.section_chars()
        row = {
            "status": status,
            "retrieval_v2_mode": mode,
            "retrieval_v2_calibration_status": run.calibration_status,
            "retrieval_v2_factual_invocation": run.factual_invocation,
            "retrieval_v2_factual_semantic_status": run.factual_semantic_status,
            "retrieval_v2_ambient_semantic_status": run.ambient_semantic_status,
            "retrieval_v2_legacy_selected": len(legacy),
            "retrieval_v2_selected": len(selected),
            "retrieval_v2_legacy_ids": [_hash_id(value) for value in sorted(legacy)[:16]],
            "retrieval_v2_selected_ids": [_hash_id(value) for value in sorted(selected)[:16]],
            "retrieval_v2_overlap_count": len(overlap),
            "retrieval_v2_overlap_rate": (
                round(len(overlap) / len(union), 4) if union else 1.0
            ),
            "retrieval_v2_candidate_factual": run.candidate_factual,
            "retrieval_v2_candidate_relation": run.candidate_relation,
            "retrieval_v2_candidate_ambient": run.candidate_ambient,
            "retrieval_v2_candidate_reaction": run.candidate_reaction,
            "retrieval_v2_selected_factual": run.selected_factual,
            "retrieval_v2_selected_relation": run.selected_relation,
            "retrieval_v2_selected_ambient": run.selected_ambient,
            "retrieval_v2_selected_reaction": run.selected_reaction,
            "retrieval_v2_factual_lexical_selected": run.factual_lexical_selected,
            "retrieval_v2_factual_semantic_selected": run.factual_semantic_only_selected,
            "retrieval_v2_zero_result": run.zero_result,
            "retrieval_v2_relation_hit": bool(run.selected_relation),
            "retrieval_v2_cache_hits": run.semantic_cache_hits,
            "retrieval_v2_cache_misses": run.semantic_cache_misses,
            "retrieval_v2_embedding_prompt_tokens": run.embedding_prompt_tokens,
            "retrieval_v2_embedding_requests": run.embedding_requests,
            "retrieval_v2_elapsed_ms": round(run.elapsed_ms),
            "retrieval_v2_composer_dedup": run.composer_dedup_candidates,
            "retrieval_v2_chars_factual": chars["factual"],
            "retrieval_v2_chars_relation": chars["relation"],
            "retrieval_v2_chars_ambient": chars["ambient"],
            "retrieval_v2_chars_reaction": chars["reaction"],
            "retrieval_v2_evidence_sufficient": run.evidence.sufficient,
            "retrieval_v2_evidence_reason": run.evidence.reason,
            "retrieval_v2_evidence_predicate": run.evidence.predicate or "",
            "retrieval_v2_evidence_state": run.evidence.answer_state,
        }
        if fallback_reason:
            row["retrieval_v2_fallback_reason"] = fallback_reason
        return row

    async def run_with_timeout(self, prepared: PreparedRetrievalV2) -> RetrievalV2Run:
        timeout = float(getattr(self.settings, "retrieval_v2_timeout_seconds", 2.0))
        async with asyncio.timeout(timeout):
            return await self.run(prepared)
