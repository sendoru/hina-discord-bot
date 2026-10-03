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

RELATIONSHIP_MIN_ITEM_CONFIDENCE = 0.0
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
        and item.confidence > min_confidence
        and item.relationship_evidence
        and memory_access(item, scope) == MemoryAccess.IMPLICIT
    ]


def _eligible_owner_relationship_observations(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return owner relationship observations eligible for the DM aggregate profile."""

    if scope.guild_id is not None:
        return []
    return [
        item
        for item in items
        if item.user_id == str(scope.user_id)
        and item.kind == MemoryKind.RELATIONSHIP
        and item.confidence > min_confidence
        and item.relationship_evidence
        and memory_access(item, scope) == MemoryAccess.FULL
    ]


def _axis_observations(
    eligible: Iterable[MemoryItem],
    axis: str,
) -> list[MemoryItem]:
    if axis not in RELATIONSHIP_EVIDENCE_AXES:
        raise ValueError(f"Unknown relationship evidence axis: {axis}")
    axis_items = [
        item
        for item in eligible
        if getattr(item.relationship_evidence, axis) > 0
    ]
    return axis_items[-RELATIONSHIP_MAX_OBSERVATIONS:]


def _profile_contributors(eligible: Iterable[MemoryItem]) -> list[MemoryItem]:
    materialized = list(eligible)
    selected_ids: set[int] = set()
    for axis in RELATIONSHIP_EVIDENCE_AXES:
        selected_ids.update(item.id for item in _axis_observations(materialized, axis))
    return [item for item in materialized if item.id in selected_ids]


def _aggregate_relationship_evidence(
    eligible: Iterable[MemoryItem],
) -> dict[str, int]:
    """Aggregate eligible relationship observations into a sparse 1..4 profile."""

    materialized = list(eligible)
    profile: dict[str, int] = {}
    for axis in RELATIONSHIP_EVIDENCE_AXES:
        candidates = _axis_observations(materialized, axis)
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

    eligible = _eligible_implicit_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    return _axis_observations(eligible, axis)


def implicit_relationship_profile_contributors(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return the union of observations used by any axis in the cross-space profile."""

    eligible = _eligible_implicit_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    return _profile_contributors(eligible)


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

    eligible = _eligible_implicit_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    return _aggregate_relationship_evidence(eligible)


def owner_relationship_axis_observations(
    items: Iterable[MemoryItem],
    scope: Scope,
    axis: str,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return the bounded newest owner-DM observations that carry one evidence axis."""

    eligible = _eligible_owner_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    return _axis_observations(eligible, axis)


def owner_relationship_profile_contributors(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> list[MemoryItem]:
    """Return the union of observations used by any axis in the owner-DM profile."""

    eligible = _eligible_owner_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    return _profile_contributors(eligible)


def aggregate_owner_relationship_evidence(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> dict[str, int]:
    """Aggregate owner-DM relationship observations with the shared profile formula."""

    eligible = _eligible_owner_relationship_observations(
        items,
        scope,
        min_confidence=min_confidence,
    )
    return _aggregate_relationship_evidence(eligible)


__all__ = [
    "RELATIONSHIP_MAX_OBSERVATIONS",
    "RELATIONSHIP_MIN_ITEM_CONFIDENCE",
    "RELATIONSHIP_RECENCY_DECAY",
    "aggregate_owner_relationship_evidence",
    "aggregate_relationship_evidence",
    "full_relationship_observations",
    "implicit_relationship_axis_observations",
    "implicit_relationship_observations",
    "implicit_relationship_profile_contributors",
    "owner_relationship_axis_observations",
    "owner_relationship_profile_contributors",
]
