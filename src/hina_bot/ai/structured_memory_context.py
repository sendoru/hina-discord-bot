"""Build privacy-bounded structured-memory context for model responses."""

from __future__ import annotations

from collections.abc import Iterable

from hina_bot.core.memory_items import (
    MemoryAccess,
    MemoryItem,
    MemoryKind,
    memory_access,
)
from hina_bot.core.relationship_profile import (
    RELATIONSHIP_MAX_OBSERVATIONS,
    RELATIONSHIP_MIN_ITEM_CONFIDENCE,
    RELATIONSHIP_RECENCY_DECAY,
    aggregate_relationship_evidence,
    implicit_relationship_observations,
)
from hina_bot.core.routing import Scope


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


def structured_memory_provenance(
    store,
    scope: Scope,
    *,
    use_memory: bool,
    allow_cross_space: bool,
    authorized_factual_items: Iterable[MemoryItem] = (),
) -> dict:
    """Return content-free provenance for the structured memory admitted to a response."""

    if not use_memory:
        return {"items": [], "relationship_axes": []}
    reader = getattr(store, "memory_items", None)
    if not callable(reader):
        return {"items": [], "relationship_axes": []}

    items = _owned(reader(scope.user_id), scope)
    authorized_ids = {
        item.id
        for item in authorized_factual_items
        if item.user_id == str(scope.user_id)
    }
    selected: list[dict] = []
    if scope.guild_id is None:
        for item in items:
            if memory_access(item, scope) != MemoryAccess.FULL:
                continue
            selected.append({
                "item_id": item.id,
                "projection": "owner_dm",
                "kind": item.kind.value,
                "disclosure": item.disclosure.value,
                "origin_realm": item.origin_realm,
                "origin_channel_id": item.origin_channel_id,
                "access": MemoryAccess.FULL.value,
            })
        return {"items": selected, "relationship_axes": []}

    full_items = [
        item
        for item in items
        if item.kind == MemoryKind.RELATIONSHIP
        and memory_access(item, scope) == MemoryAccess.FULL
    ][-RELATIONSHIP_MAX_OBSERVATIONS:]
    for item in full_items:
        selected.append({
            "item_id": item.id,
            "projection": "relationship_full",
            "kind": item.kind.value,
            "disclosure": item.disclosure.value,
            "origin_realm": item.origin_realm,
            "origin_channel_id": item.origin_channel_id,
            "access": MemoryAccess.FULL.value,
        })

    if not allow_cross_space:
        return {"items": selected, "relationship_axes": []}

    implicit_items = implicit_relationship_observations(items, scope)
    for item in implicit_items:
        selected.append({
            "item_id": item.id,
            "projection": "relationship_evidence",
            "kind": item.kind.value,
            "disclosure": item.disclosure.value,
            "origin_realm": item.origin_realm,
            "origin_channel_id": item.origin_channel_id,
            "access": MemoryAccess.IMPLICIT.value,
        })

    for item in items:
        if item.id not in authorized_ids:
            continue
        selected.append({
            "item_id": item.id,
            "projection": "authorized_factual_recall",
            "kind": item.kind.value,
            "disclosure": item.disclosure.value,
            "origin_realm": item.origin_realm,
            "origin_channel_id": item.origin_channel_id,
            "access": MemoryAccess.FULL.value,
            "authorization_reason": "owner_explicit_reference",
        })

    profile = aggregate_relationship_evidence(items, scope)
    return {
        "items": selected,
        "relationship_axes": sorted(profile),
    }


def structured_memory_context(
    store,
    scope: Scope,
    *,
    use_memory: bool,
    allow_cross_space: bool,
    authorized_factual_items: Iterable[MemoryItem] = (),
) -> dict:
    """Build structured fields safe to serialize into the response request."""

    empty = {
        "structured_owner_memory": [],
        "structured_relationship_memory": [],
        "cross_space_relationship": {},
        "authorized_factual_memory": [],
    }
    if not use_memory:
        return empty

    reader = getattr(store, "memory_items", None)
    if not callable(reader):
        return empty

    items = reader(scope.user_id)
    authorized_ids = {
        item.id
        for item in authorized_factual_items
        if item.user_id == str(scope.user_id)
    }
    authorized = [
        {
            **_serialize_memory_item(item),
            "authorization": "owner_explicit_reference",
        }
        for item in items
        if item.id in authorized_ids
    ]
    return {
        "structured_owner_memory": owner_dm_memory(items, scope),
        "structured_relationship_memory": full_relationship_memory(items, scope),
        "cross_space_relationship": (
            aggregate_relationship_evidence(items, scope)
            if allow_cross_space
            else {}
        ),
        "authorized_factual_memory": authorized,
    }


__all__ = [
    "RELATIONSHIP_MAX_OBSERVATIONS",
    "RELATIONSHIP_MIN_ITEM_CONFIDENCE",
    "RELATIONSHIP_RECENCY_DECAY",
    "aggregate_relationship_evidence",
    "full_relationship_memory",
    "owner_dm_memory",
    "structured_memory_context",
    "structured_memory_provenance",
]
