"""Opt-in scene-aware ambient character insight retrieval for Retrieval v2."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from time import perf_counter

from .knowledge_retrieval import KnowledgeCandidate, KnowledgeUsage, RankedKnowledgeCandidate
from .retrieval_v2 import RetrievalRequest
from .semantic_retrieval import SemanticCalibration, SemanticIndex

_MAX_VISIBLE_CHARS = 600
_MAX_ANCHOR_CHARS = 600
_MAX_RECENT_TURNS = 2
_MAX_RECENT_CHARS = 400
_MAX_RELATIONSHIP_CHARS = 240

AMBIENT_CONTEXT_POLICY = (
    "character_insights는 검수된 해석 자료이며 공식 사실 목록이 아닙니다. "
    "현재 장면과 맞을 때 말투·행동·반응을 자연스럽게 형성하는 참고로만 사용하고, "
    "반드시 직접 설명하거나 자기분석 문장으로 말하지 않습니다. "
    "현재 대화의 직접적인 evidence가 있으면 그것을 우선합니다."
)


@dataclass(frozen=True)
class AmbientSceneContext:
    """Bounded scene signals authorized by the caller.

    rp_entity is a canonical filter only and is never embedded as pseudo-natural text.
    recent_same_speaker must contain only already-authorized recent turns for the current
    speaker; the retriever keeps at most the newest two.
    """

    rp_entity: str
    recent_same_speaker: tuple[str, ...] = ()
    relationship_signal: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"character\.[a-z0-9_.-]+", self.rp_entity):
            raise ValueError("rp_entity must be a canonical character id")


@dataclass(frozen=True)
class AmbientConfig:
    """Precision-first semantic admission; defaults are provisional until live eval."""

    calibration: SemanticCalibration | None = None
    semantic_min: float = 0.75
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not isfinite(self.semantic_min) or not 0 < self.semantic_min <= 1:
            raise ValueError("invalid ambient semantic threshold")
        if not 0 < self.timeout_seconds <= 600:
            raise ValueError("invalid ambient semantic deadline")


@dataclass(frozen=True)
class AmbientResult:
    rows: tuple[RankedKnowledgeCandidate, ...]
    semantic_status: str
    cache_hits: int = 0
    cache_misses: int = 0
    elapsed_ms: float = 0.0


def _bounded(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _meaning_key(value: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value).casefold())


def ambient_query(request: RetrievalRequest, scene: AmbientSceneContext) -> str:
    """Build a small scene representation without lexical hints, memory dumps or ids."""
    current = _bounded(request.visible_text, _MAX_VISIBLE_CHARS)
    if not current:
        return ""

    parts: list[str] = []
    seen: set[str] = set()

    def add(value: str, limit: int) -> None:
        text = _bounded(value, limit)
        key = _meaning_key(text)
        if not text or not key or key in seen:
            return
        parts.append(text)
        seen.add(key)

    # Current turn is primary. Causal anchor and same-speaker turns only add local scene
    # context. Relationship projection is a caller-supplied small natural-language signal.
    add(current, _MAX_VISIBLE_CHARS)
    add(request.anchor_text, _MAX_ANCHOR_CHARS)
    for turn in scene.recent_same_speaker[-_MAX_RECENT_TURNS:]:
        add(turn, _MAX_RECENT_CHARS)
    add(scene.relationship_signal, _MAX_RELATIONSHIP_CHARS)
    return "\n".join(parts)


def eligible_ambient_candidates(
    scene: AmbientSceneContext,
    candidates: Sequence[KnowledgeCandidate],
) -> list[KnowledgeCandidate]:
    """Select reviewed ambient interpretations for the current RP entity."""
    result = []
    seen = set()
    for candidate in candidates:
        if KnowledgeUsage.AMBIENT not in candidate.retrieval_usages:
            continue
        if candidate.fact_type != "inference":
            continue
        if scene.rp_entity not in candidate.entities:
            continue
        identity = (candidate.source, candidate.candidate_id)
        if identity in seen:
            raise ValueError("duplicate ambient candidate identity")
        seen.add(identity)
        result.append(candidate)
    return result


class AmbientRetriever:
    """Semantic-only ambient retrieval: uncertainty yields zero insight, never web/lexical."""

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
        scene: AmbientSceneContext,
        candidates: Sequence[KnowledgeCandidate],
    ) -> AmbientResult:
        started = perf_counter()
        rows = eligible_ambient_candidates(scene, candidates)
        query = ambient_query(request, scene)
        config = self.config
        if not query or not rows:
            return AmbientResult((), "not_needed")
        if self.index is None or config.calibration is None:
            return AmbientResult((), "unavailable")
        if config.calibration.backend_key != self.index.backend.cache_key:
            return AmbientResult((), "calibration_mismatch")

        try:
            async with asyncio.timeout(config.timeout_seconds):
                search = await self.index.search(query, rows, top_k=len(rows))
        except Exception:  # noqa: BLE001 - optional ambient context must never fail an answer
            return AmbientResult(
                (),
                "failed",
                elapsed_ms=(perf_counter() - started) * 1000,
            )

        ranked = []
        for hit in search.hits:
            score = config.calibration.score(hit.cosine)
            if score < config.semantic_min:
                continue
            ranked.append(RankedKnowledgeCandidate(score, hit.order, rows[hit.order]))
        return AmbientResult(
            tuple(ranked),
            "available",
            search.cache_hits,
            search.cache_misses,
            (perf_counter() - started) * 1000,
        )
