"""Shared effective relationship-profile calculation."""

from __future__ import annotations

from collections.abc import Iterable

from .memory_items import (
    RELATIONSHIP_EVIDENCE_AXES,
    MemoryAccess,
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    memory_access,
)
from .routing import Scope

RELATIONSHIP_MIN_ITEM_CONFIDENCE = 0.8
RELATIONSHIP_MAX_OBSERVATIONS = 8
RELATIONSHIP_RECENCY_DECAY = 0.85


def full_relationship_observations(
    items: Iterable[MemoryItem],
    scope: Scope,
) -> list[MemoryItem]:
    """Return the exact bounded raw relationship items admitted as FULL in shared space."""

    if scope.guild_id is None:
        return []
    owned = [
        item
        for item in items
        if item.user_id == str(scope.user_id)
        and item.kind == MemoryKind.RELATIONSHIP
        and memory_access(item, scope) == MemoryAccess.FULL
    ]
    return owned[-RELATIONSHIP_MAX_OBSERVATIONS:]


def _eligible_implicit_relationship_observations(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return all observations eligible for cross-space relationship projection."""

    if scope.guild_id is None:
        return []
    return [
        item
        for item in items
        if item.user_id == str(scope.user_id)
        and item.kind == MemoryKind.RELATIONSHIP
        and item.disclosure == MemoryDisclosure.IMPLICIT
        and item.confidence >= min_confidence
        and item.relationship_evidence
        and memory_access(item, scope) == MemoryAccess.IMPLICIT
    ]


def implicit_relationship_observations(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return the bounded newest implicit observations, regardless of evidence axis."""

    eligible = _eligible_implicit_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    return eligible[-RELATIONSHIP_MAX_OBSERVATIONS:]


def implicit_relationship_axis_observations(
    items: Iterable[MemoryItem],
    scope: Scope,
    axis: str,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return the bounded newest implicit observations that carry one evidence axis."""

    if axis not in RELATIONSHIP_EVIDENCE_AXES:
        raise ValueError(f"Unknown relationship evidence axis: {axis}")
    eligible = _eligible_implicit_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    axis_items = [
        item
        for item in eligible
        if getattr(item.relationship_evidence, axis) > 0
    ]
    return axis_items[-RELATIONSHIP_MAX_OBSERVATIONS:]


def implicit_relationship_profile_contributors(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return the union of observations used by any axis in the effective profile."""

    eligible = _eligible_implicit_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    selected_ids: set[int] = set()
    for axis in RELATIONSHIP_EVIDENCE_AXES:
        axis_items = [
            item
            for item in eligible
            if getattr(item.relationship_evidence, axis) > 0
        ]
        selected_ids.update(
            item.id for item in axis_items[-RELATIONSHIP_MAX_OBSERVATIONS:]
        )
    return [item for item in eligible if item.id in selected_ids]


def aggregate_relationship_evidence(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> dict[str, int]:
    """Aggregate cross-space implicit relationship observations into a 1..4 profile.

    Each item stores batch-local positive evidence, not a global relationship state. Recent
    evidence is combined with confidence and exponential recency decay using noisy-OR.
    Missing axes remain missing; zero never means dislike or rejection.
    """

    materialized = list(items)

    profile: dict[str, int] = {}
    for axis in RELATIONSHIP_EVIDENCE_AXES:
        candidates = implicit_relationship_axis_observations(
            materialized,
            scope,
            axis,
            min_confidence=min_confidence,
        )
        if not candidates:
            continue

        remaining = 1.0
        for age, item in enumerate(reversed(candidates)):
            level = getattr(item.relationship_evidence, axis)
            recency = RELATIONSHIP_RECENCY_DECAY ** age
            contribution = min(
                1.0,
                (level / 4.0) * item.confidence * recency,
            )
            remaining *= 1.0 - contribution
        combined = 1.0 - remaining
        profile[axis] = max(1, min(4, int(combined * 4.0 + 0.5)))
    return profile


__all__ = [
    "RELATIONSHIP_MAX_OBSERVATIONS",
    "RELATIONSHIP_MIN_ITEM_CONFIDENCE",
    "RELATIONSHIP_RECENCY_DECAY",
    "aggregate_relationship_evidence",
    "full_relationship_observations",
    "implicit_relationship_axis_observations",
    "implicit_relationship_observations",
    "implicit_relationship_profile_contributors",
]
