"""Scene-aware ambient character insight retrieval for Retrieval v2.

This module is opt-in and local-knowledge-only. It returns admitted rows; BundleComposer
owns final packing/deduplication and #316 owns production invocation.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from time import perf_counter

from .entity_resolution import HINA_ENTITY_ID
from .knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
)
from .memory_items import RELATIONSHIP_EVIDENCE_AXES
from .retrieval_v2 import RetrievalRequest
from .semantic_retrieval import SemanticCalibration, SemanticHit, SemanticIndex

_RECENT_TURNS_MAX = 2
_SEGMENT_MAX_CHARS = 320
_QUERY_MAX_CHARS = 1200

_AXIS_LABELS = {
    "familiarity": "익숙함",
    "comfort": "편안함",
    "casualness": "편한 상호작용",
    "teasing_tolerance": "장난 수용",
    "support_openness": "도움 수용",
    "task_orientation": "업무 중심",
}


@dataclass(frozen=True)
class AmbientConfig:
    """Precision-first ambient policy; calibration remains explicit and opt-in."""

    calibration: SemanticCalibration | None = None
    min_score: float = 0.75
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not 0 < self.min_score <= 1:
            raise ValueError("ambient min_score must be in (0, 1]")
        if not isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("invalid ambient semantic deadline")


@dataclass(frozen=True)
class AmbientResult:
    rows: tuple[RankedKnowledgeCandidate, ...]
    semantic_status: str
    cache_hits: int = 0
    cache_misses: int = 0
    elapsed_ms: float = 0.0


def _meaning_key(text: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text).casefold())


def _clip(text: str) -> str:
    return " ".join(text.split())[:_SEGMENT_MAX_CHARS]


def _relationship_signal(relationship_axes: Mapping[str, int] | None) -> str:
    if not relationship_axes:
        return ""
    unknown = set(relationship_axes) - set(RELATIONSHIP_EVIDENCE_AXES)
    if unknown:
        raise ValueError("unknown relationship evidence axis")
    values = []
    for axis in RELATIONSHIP_EVIDENCE_AXES:
        value = relationship_axes.get(axis, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
            raise ValueError("relationship evidence must be an integer in 0..4")
        if value:
            values.append(f"{_AXIS_LABELS[axis]} {value}/4")
    return ", ".join(values)


def ambient_query(
    request: RetrievalRequest,
    *,
    recent_same_speaker: Sequence[str] = (),
    relationship_axes: Mapping[str, int] | None = None,
) -> str:
    """Build a bounded scene representation without lexical expansions or raw memory.

    Inputs are limited to the visible turn, authorized causal anchor, at most two recent
    same-speaker turns supplied by the caller, and a small aggregate relationship signal.
    Canonical entity ids are filters, not embedding text.
    """
    if len(recent_same_speaker) > _RECENT_TURNS_MAX:
        raise ValueError("ambient scene accepts at most two recent same-speaker turns")

    current = _clip(request.visible_text)
    if not current:
        return ""

    segments: list[tuple[str, str]] = []
    seen = {_meaning_key(current)}

    for text in recent_same_speaker:
        clipped = _clip(text)
        key = _meaning_key(clipped)
        if clipped and key and key not in seen:
            segments.append(("최근 같은 화자", clipped))
            seen.add(key)

    anchor = _clip(request.anchor_text)
    anchor_key = _meaning_key(anchor)
    if anchor and anchor_key and anchor_key not in seen:
        segments.append(("관련 맥락", anchor))
        seen.add(anchor_key)

    signal = _relationship_signal(relationship_axes)
    if signal:
        segments.append(("관계 신호", signal))

    segments.append(("현재 발화", current))
    rendered = "\n".join(f"{label}: {value}" for label, value in segments)
    return rendered[:_QUERY_MAX_CHARS]


def eligible_ambient_candidates(
    candidates: Sequence[KnowledgeCandidate],
    *,
    rp_entity: str = HINA_ENTITY_ID,
) -> list[KnowledgeCandidate]:
    """Keep reviewed Hina ambient interpretations only; ambiguity becomes zero results."""
    seen = set()
    rows = []
    for row in candidates:
        if (
            KnowledgeUsage.AMBIENT not in row.retrieval_usages
            or row.lane != "canon"
            or row.fact_type != "inference"
            or row.awareness != "inference"
            or rp_entity not in row.entities
        ):
            continue
        identity = (row.source, row.candidate_id)
        if identity in seen:
            raise ValueError("duplicate ambient candidate identity")
        seen.add(identity)
        rows.append(row)
    return rows


def rank_ambient(
    candidates: Sequence[KnowledgeCandidate],
    hits: Sequence[SemanticHit],
    config: AmbientConfig,
) -> tuple[RankedKnowledgeCandidate, ...]:
    """Apply calibrated ambient admission to cosine-ordered full-corpus hits."""
    if config.calibration is None:
        return ()
    admitted = []
    for hit in hits:
        score = config.calibration.score(hit.cosine)
        if score < config.min_score:
            continue
        admitted.append(
            RankedKnowledgeCandidate(score, hit.order, candidates[hit.order])
        )
    return tuple(admitted)


class AmbientRetriever:
    """Semantic-only, precision-first ambient retrieval with no lexical/web fallback."""

    def __init__(
        self,
        index: SemanticIndex | None = None,
        *,
        config: AmbientConfig | None = None,
    ):
        self.index = index
        self.config = config or AmbientConfig()

    async def retrieve(
        self,
        request: RetrievalRequest,
        candidates: Sequence[KnowledgeCandidate],
        *,
        rp_entity: str = HINA_ENTITY_ID,
        recent_same_speaker: Sequence[str] = (),
        relationship_axes: Mapping[str, int] | None = None,
    ) -> AmbientResult:
        started = perf_counter()
        config = self.config
        rows = eligible_ambient_candidates(candidates, rp_entity=rp_entity)
        query = ambient_query(
            request,
            recent_same_speaker=recent_same_speaker,
            relationship_axes=relationship_axes,
        )
        if not query or not rows:
            return AmbientResult((), "not_needed")

        if self.index is None or config.calibration is None:
            return AmbientResult((), "unavailable")
        if config.calibration.backend_key != self.index.backend.cache_key:
            return AmbientResult((), "calibration_mismatch")

        try:
            async with asyncio.timeout(config.timeout_seconds):
                result = await self.index.search(query, rows, top_k=len(rows))
        except Exception:  # noqa: BLE001 - ambient failure must degrade to no insight
            return AmbientResult(
                (),
                "failed",
                elapsed_ms=(perf_counter() - started) * 1000,
            )

        admitted = rank_ambient(rows, result.hits, config)

        return AmbientResult(
            admitted,
            "available",
            result.cache_hits,
            result.cache_misses,
            (perf_counter() - started) * 1000,
        )
