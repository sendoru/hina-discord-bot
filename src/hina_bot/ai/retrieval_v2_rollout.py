"""Retrieval v2 rollout controller and privacy-bounded scene construction."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from hina_bot.core.ambient_retrieval import AmbientSceneContext
from hina_bot.core.entity_resolution import HINA_ENTITY_ID
from hina_bot.core.retrieval_v2_runtime import (
    RetrievalV2Budgets,
    RetrievalV2Engine,
    RetrievalV2Result,
)
from hina_bot.core.semantic_retrieval import SemanticCalibration, SemanticIndex

from .embedding_backend import GeminiEmbeddingBackend, GeminiEmbeddingConfig
from .structured_memory_context import structured_memory_context

_RELATIONSHIP_LABELS = {
    "familiarity": "익숙함",
    "comfort": "편안함",
    "casualness": "편한 상호작용",
    "teasing_tolerance": "장난 수용",
    "support_openness": "도움 수용",
    "task_orientation": "업무 중심",
}


@dataclass(frozen=True)
class RetrievalV2Run:
    result: RetrievalV2Result | None
    status: str
    fallback_reason: str = ""


class RetrievalV2Controller:
    """Own the optional embedding backend and enforce rollout calibration gates."""

    def __init__(self, settings, lore):
        self.settings = settings
        self.backend = None
        self.semantic_index = None
        self.semantic_gate_reason = "gemini_key_missing"
        factual_calibration = ambient_calibration = None

        api_key = str(getattr(settings, "gemini_api_key", "") or "").strip()
        if api_key:
            config = GeminiEmbeddingConfig(
                dimensions=int(settings.retrieval_v2_embedding_dimensions),
                timeout_seconds=min(
                    30.0,
                    max(0.25, float(settings.retrieval_v2_timeout_seconds)),
                ),
            )
            self.backend = GeminiEmbeddingBackend(api_key, config=config)
            expected = str(
                getattr(settings, "retrieval_v2_calibration_backend_key", "") or ""
            ).strip()
            if not expected:
                self.semantic_gate_reason = "calibration_backend_key_missing"
            elif expected != self.backend.cache_key:
                self.semantic_gate_reason = "calibration_backend_key_mismatch"
            else:
                factual_pair = (
                    settings.retrieval_v2_factual_reject,
                    settings.retrieval_v2_factual_strong,
                )
                ambient_pair = (
                    settings.retrieval_v2_ambient_reject,
                    settings.retrieval_v2_ambient_strong,
                )
                if any(value is None for value in (*factual_pair, *ambient_pair)):
                    self.semantic_gate_reason = "calibration_thresholds_missing"
                else:
                    factual_calibration = SemanticCalibration(
                        self.backend.cache_key,
                        factual_pair[0],
                        factual_pair[1],
                    )
                    ambient_calibration = SemanticCalibration(
                        self.backend.cache_key,
                        ambient_pair[0],
                        ambient_pair[1],
                    )
                    self.semantic_index = SemanticIndex(self.backend)
                    self.semantic_gate_reason = "ready"

        self.engine = RetrievalV2Engine(
            lore,
            semantic_index=self.semantic_index,
            factual_calibration=factual_calibration,
            ambient_calibration=ambient_calibration,
        )

    @property
    def semantic_ready(self) -> bool:
        return self.semantic_gate_reason == "ready"

    @property
    def active_ready(self) -> bool:
        # Active rollout intentionally requires live-calibrated factual + ambient semantics.
        return self.semantic_ready

    async def close(self) -> None:
        if self.backend is not None:
            await self.backend.close()

    def budgets(self) -> RetrievalV2Budgets:
        max_items = max(0, int(self.settings.lore_max_items))
        max_chars = max(0, int(self.settings.lore_max_chars))
        return RetrievalV2Budgets(
            max_items=max_items,
            max_chars=max_chars,
            relation_items=min(4, max_items),
            ambient_items=min(2, max_items),
            ambient_chars=min(900, max_chars),
            reaction_items=min(1, max_items),
        )

    async def retrieve(
        self,
        request,
        *,
        runtime_candidates=(),
        scene=None,
    ) -> RetrievalV2Run:
        try:
            async with asyncio.timeout(float(self.settings.retrieval_v2_timeout_seconds)):
                result = await self.engine.retrieve(
                    request,
                    runtime_candidates=runtime_candidates,
                    scene=scene,
                    include_community=bool(self.settings.community_lore),
                    budgets=self.budgets(),
                )
            return RetrievalV2Run(result, "completed")
        except TimeoutError:
            return RetrievalV2Run(None, "timeout", "overall_timeout")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - rollout fallback must not expose provider/content errors
            return RetrievalV2Run(None, "failed", "runtime_error")


def _recent_same_speaker(store, scope, channel_context, *, use_memory: bool) -> tuple[str, ...]:
    current = str(scope.user_id)
    rows = [
        row for row in channel_context or ()
        if row.get("role") == "user"
        and str(row.get("author_user_id") or row.get("user_id") or "") == current
        and isinstance(row.get("content"), str)
        and row["content"].strip()
    ]
    return tuple(row["content"] for row in rows[-2:])


def _relationship_signal(store, scope, *, use_memory: bool) -> str:
    if not use_memory:
        return ""
    context = structured_memory_context(
        store,
        scope,
        use_memory=True,
        allow_cross_space=True,
    )
    profile = (
        context.get("owner_relationship_profile", {})
        if scope.guild_id is None
        else context.get("cross_space_relationship", {})
    )
    if not isinstance(profile, dict):
        return ""
    parts = [
        f"{_RELATIONSHIP_LABELS.get(axis, axis)} {level}/4"
        for axis, level in profile.items()
        if axis in _RELATIONSHIP_LABELS
        and type(level) is int
        and 1 <= level <= 4
    ]
    return ", ".join(parts)


def build_ambient_scene(
    store,
    scope,
    channel_context,
    *,
    use_memory: bool,
) -> AmbientSceneContext:
    """Build only already-authorized same-speaker and aggregate relationship signals."""
    return AmbientSceneContext(
        rp_entity=HINA_ENTITY_ID,
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
