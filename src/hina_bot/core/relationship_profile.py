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


def implicit_relationship_observations(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return the exact bounded observations eligible for cross-space projection."""

    if scope.guild_id is None:
        return []
    owned = [
        item
        for item in items
        if item.user_id == str(scope.user_id)
        and item.kind == MemoryKind.RELATIONSHIP
        and item.disclosure == MemoryDisclosure.IMPLICIT
        and item.confidence >= min_confidence
        and item.relationship_evidence
        and memory_access(item, scope) == MemoryAccess.IMPLICIT
    ]
    return owned[-RELATIONSHIP_MAX_OBSERVATIONS:]


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

    candidates = implicit_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    newest_first = list(reversed(candidates))

    profile: dict[str, int] = {}
    for axis in RELATIONSHIP_EVIDENCE_AXES:
        remaining = 1.0
        seen = False
        for age, item in enumerate(newest_first):
            level = getattr(item.relationship_evidence, axis)
            if level <= 0:
                continue
            seen = True
            recency = RELATIONSHIP_RECENCY_DECAY ** age
            contribution = min(
                1.0,
                (level / 4.0) * item.confidence * recency,
            )
            remaining *= 1.0 - contribution
        if not seen:
            continue
        combined = 1.0 - remaining
        profile[axis] = max(1, min(4, int(combined * 4.0 + 0.5)))
    return profile


__all__ = [
    "RELATIONSHIP_MAX_OBSERVATIONS",
    "RELATIONSHIP_MIN_ITEM_CONFIDENCE",
    "RELATIONSHIP_RECENCY_DECAY",
    "aggregate_relationship_evidence",
    "full_relationship_observations",
    "implicit_relationship_observations",
]
