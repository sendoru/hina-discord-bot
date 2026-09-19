"""Build privacy-bounded structured-memory context for model responses."""

from __future__ import annotations

from collections.abc import Iterable

from hina_bot.core.memory_items import (
    RELATIONSHIP_EVIDENCE_AXES,
    MemoryAccess,
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    memory_access,
)
from hina_bot.core.routing import Scope

RELATIONSHIP_MIN_ITEM_CONFIDENCE = 0.8
RELATIONSHIP_MAX_OBSERVATIONS = 8
RELATIONSHIP_RECENCY_DECAY = 0.85


def _owned(items: Iterable[MemoryItem], scope: Scope) -> list[MemoryItem]:
    owner = str(scope.user_id)
    return [item for item in items if item.user_id == owner]


def _serialize_memory_item(item: MemoryItem) -> dict:
    row = {
        "kind": item.kind.value,
        "content": item.content,
        "confidence": item.confidence,
    }
    evidence = item.relationship_evidence.as_dict()
    if evidence:
        row["relationship_evidence"] = evidence
    return row


def owner_dm_memory(items: Iterable[MemoryItem], scope: Scope) -> list[dict]:
    """Serialize all structured memory owned by the current user only in their DM."""

    if scope.guild_id is not None:
        return []
    return [
        _serialize_memory_item(item)
        for item in _owned(items, scope)
        if memory_access(item, scope) == MemoryAccess.FULL
    ]


def full_relationship_memory(items: Iterable[MemoryItem], scope: Scope) -> list[dict]:
    """Return bounded raw relationship items that are FULL in the current shared space."""

    if scope.guild_id is None:
        return []
    candidates = [
        item
        for item in _owned(items, scope)
        if item.kind == MemoryKind.RELATIONSHIP
        and memory_access(item, scope) == MemoryAccess.FULL
    ]
    return [
        _serialize_memory_item(item)
        for item in candidates[-RELATIONSHIP_MAX_OBSERVATIONS:]
    ]


def aggregate_relationship_evidence(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = RELATIONSHIP_MIN_ITEM_CONFIDENCE,
) -> dict[str, int]:
    """Aggregate cross-space implicit relationship observations into a 1..4 profile.

    Each item stores batch-local positive evidence, not a global relationship state. We
    combine recent evidence with confidence and exponential recency decay using noisy-OR,
    so repeated weak observations can accumulate without any one batch overwriting the
    profile. Missing axes remain missing; zero never means dislike or rejection.
    """

    if scope.guild_id is None:
        return {}

    candidates = [
        item
        for item in _owned(items, scope)
        if item.kind == MemoryKind.RELATIONSHIP
        and item.disclosure == MemoryDisclosure.IMPLICIT
        and item.confidence >= min_confidence
        and item.relationship_evidence
        and memory_access(item, scope) == MemoryAccess.IMPLICIT
    ][-RELATIONSHIP_MAX_OBSERVATIONS:]
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
        level = max(1, min(4, int(combined * 4.0 + 0.5)))
        profile[axis] = level
    return profile


def structured_memory_context(
    store,
    scope: Scope,
    *,
    use_memory: bool,
    allow_cross_space: bool,
) -> dict:
    """Build structured fields safe to serialize into the response request."""

    empty = {
        "structured_owner_memory": [],
        "structured_relationship_memory": [],
        "cross_space_relationship": {},
    }
    if not use_memory:
        return empty

    reader = getattr(store, "memory_items", None)
    if not callable(reader):
        return empty

    items = reader(scope.user_id)
    return {
        "structured_owner_memory": owner_dm_memory(items, scope),
        "structured_relationship_memory": full_relationship_memory(items, scope),
        "cross_space_relationship": (
            aggregate_relationship_evidence(items, scope)
            if allow_cross_space
            else {}
        ),
    }


__all__ = [
    "RELATIONSHIP_MAX_OBSERVATIONS",
    "RELATIONSHIP_MIN_ITEM_CONFIDENCE",
    "RELATIONSHIP_RECENCY_DECAY",
    "aggregate_relationship_evidence",
    "full_relationship_memory",
    "owner_dm_memory",
    "structured_memory_context",
]
