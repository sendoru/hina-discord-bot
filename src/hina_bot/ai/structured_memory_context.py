"""Build the structured-memory portion of response context.

The response layer intentionally separates two cases:

* In the owner's DM, all structured memories owned by that user may be supplied in full.
* In a shared space, cross-space implicit relationship memories may affect only a
  bounded relationship signal. Their raw text never reaches the response model.

Reference-gated factual recall in shared spaces remains a later phase.
"""

from __future__ import annotations

from collections.abc import Iterable

from hina_bot.core.memory_items import (
    MemoryAccess,
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    memory_access,
)
from hina_bot.core.routing import Scope

IMPLICIT_RELATIONSHIP_MIN_CONFIDENCE = 0.8


def _owned(items: Iterable[MemoryItem], scope: Scope) -> list[MemoryItem]:
    owner = str(scope.user_id)
    return [item for item in items if item.user_id == owner]


def owner_dm_memory(items: Iterable[MemoryItem], scope: Scope) -> list[dict]:
    """Serialize all of this owner\'s structured memory only in their DM."""

    if scope.guild_id is not None:
        return []
    return [
        {
            "kind": item.kind.value,
            "content": item.content,
            "confidence": item.confidence,
        }
        for item in _owned(items, scope)
        if memory_access(item, scope) == MemoryAccess.FULL
    ]


def implicit_relationship_projection(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    min_confidence: float = IMPLICIT_RELATIONSHIP_MIN_CONFIDENCE,
) -> dict:
    """Return a bounded cross-space relationship signal without raw memory text."""

    if scope.guild_id is None:
        return {}
    for item in _owned(items, scope):
        if item.kind != MemoryKind.RELATIONSHIP:
            continue
        if item.disclosure != MemoryDisclosure.IMPLICIT:
            continue
        if item.confidence < min_confidence:
            continue
        if memory_access(item, scope) == MemoryAccess.IMPLICIT:
            return {"familiarity": "established"}
    return {}


def structured_memory_context(
    store,
    scope: Scope,
    *,
    use_memory: bool,
    allow_cross_space: bool,
) -> dict:
    """Build the fields that are safe to serialize into the response request."""

    if not use_memory:
        return {
            "structured_owner_memory": [],
            "cross_space_relationship": {},
        }

    items = store.memory_items(scope.user_id)
    return {
        "structured_owner_memory": owner_dm_memory(items, scope),
        "cross_space_relationship": (
            implicit_relationship_projection(items, scope)
            if allow_cross_space
            else {}
        ),
    }


__all__ = [
    "IMPLICIT_RELATIONSHIP_MIN_CONFIDENCE",
    "implicit_relationship_projection",
    "owner_dm_memory",
    "structured_memory_context",
]
