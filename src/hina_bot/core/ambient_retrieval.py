"""Scene-aware semantic retrieval for reviewed ambient character insights."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from time import perf_counter

from .knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
)
from .retrieval_v2 import RetrievalRequest
from .semantic_retrieval import SemanticCalibration, SemanticIndex, semantic_query


@dataclass(frozen=True)
class AmbientScene:
    """Small authorized scene projection; never a transcript or memory dump."""

    request: RetrievalRequest
    rp_entity: str
    recent_turns: tuple[str, ...] = ()
    relationship_signal: str = ""

    def __post_init__(self) -> None:
        if not self.rp_entity:
            raise ValueError("ambient scene requires an RP entity")
        if len(self.recent_turns) > 2:
            raise ValueError("ambient scene accepts at most two recent turns")
        if any(not turn.strip() for turn in self.recent_turns):
            raise ValueError("ambient recent turns must be non-empty")
        if len(self.relationship_signal) > 300:
            raise ValueError("ambient relationship signal is too long")


@dataclass(frozen=True)
class AmbientConfig:
    """Precision-first semantic admission; live calibration is explicit and optional."""

    calibration: SemanticCalibration | None = None
    semantic_min: float = 0.75
    timeout_seconds: float = 30.0

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


def _meaning_key(value: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value).casefold())


def ambient_scene_query(scene: AmbientScene) -> str:
    """Natural scene meaning only; no lexical expansions, ids, prompts or memory dumps."""
    base = semantic_query(scene.request)
    if not base:
        return ""

    parts: list[str] = []
    keys: list[str] = []

    def append_unique(value: str) -> None:
        text = " ".join(value.split())
        key = _meaning_key(text)
        if not key:
            return
        if any(key in old or old in key for old in keys):
            return
        parts.append(text)
        keys.append(key)

    for turn in scene.recent_turns:
        append_unique(turn)
    # semantic_query already contains the authorized causal anchor when it is not
    # semantically contained in the visible message.
    for piece in base.splitlines():
        append_unique(piece)
    append_unique(scene.relationship_signal)
    return "\n".join(parts)


def eligible_ambient_candidates(
    candidates: Sequence[KnowledgeCandidate],
    *,
    rp_entity: str,
) -> list[KnowledgeCandidate]:
    """Use only reviewed, evidence-linked canon interpretations for the current RP entity."""
    seen = set()
    rows = []
    for candidate in candidates:
        if (
            KnowledgeUsage.AMBIENT not in candidate.retrieval_usages
            or candidate.lane != "canon"
            or candidate.fact_type != "inference"
            or rp_entity not in candidate.entities
            or not candidate.evidence_ids
        ):
            continue
        identity = (candidate.source, candidate.candidate_id)
        if identity in seen:
            raise ValueError("duplicate ambient candidate identity")
        seen.add(identity)
        rows.append(candidate)
    return rows


class AmbientRetriever:
    """Semantic-only ambient activation; absence/failure produces no insight."""

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
        scene: AmbientScene,
        candidates: Sequence[KnowledgeCandidate],
    ) -> AmbientResult:
        started = perf_counter()
        rows = eligible_ambient_candidates(candidates, rp_entity=scene.rp_entity)
        query = ambient_scene_query(scene)
        if not query or not rows:
            return AmbientResult((), "not_needed")

        calibration = self.config.calibration
        if self.index is None or calibration is None:
            return AmbientResult((), "unavailable")
        if calibration.backend_key != self.index.backend.cache_key:
            return AmbientResult((), "calibration_mismatch")

        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                result = await self.index.search(query, rows, top_k=len(rows))
        except Exception:  # noqa: BLE001 - optional ambient context must never fail an answer
            return AmbientResult(
                (),
                "failed",
                elapsed_ms=(perf_counter() - started) * 1000,
            )

        ranked = []
        for hit in result.hits:
            score = calibration.score(hit.cosine)
            if score < self.config.semantic_min:
                continue
            ranked.append(RankedKnowledgeCandidate(score, hit.order, rows[hit.order]))

        return AmbientResult(
            tuple(ranked),
            "available",
            result.cache_hits,
            result.cache_misses,
            (perf_counter() - started) * 1000,
        )
