"""Build privacy-bounded structured-memory context for model responses."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from hina_bot.core.memory_items import (
    MemoryAccess,
    MemoryItem,
    MemoryKind,
    memory_access,
)
from hina_bot.core.memory_selection import (
    MemorySelection,
    SelectionContext,
    render_user_memory,
    select_personal_memory,
    selection_provenance,
)
from hina_bot.core.relationship_profile import (
    RELATIONSHIP_MAX_OBSERVATIONS,
    RELATIONSHIP_MIN_ITEM_CONFIDENCE,
    RELATIONSHIP_RECENCY_DECAY,
    aggregate_owner_relationship_evidence,
    aggregate_relationship_evidence,
    aggregate_shared_relationship_evidence,
    full_relationship_observations,
    implicit_relationship_profile_contributors,
)
from hina_bot.core.routing import Scope


def _owned(items: Iterable[MemoryItem], scope: Scope) -> list[MemoryItem]:
    owner = str(scope.user_id)
    return [item for item in items if item.user_id == owner]


def _scope_relation(item: MemoryItem, scope: Scope) -> str:
    if (
        item.origin_realm == scope.realm
        and item.origin_channel_id == str(scope.channel_id)
    ):
        return "current_channel"
    if (
        scope.guild_id is not None
        and item.origin_realm == scope.realm
        and item.origin_realm.startswith("guild:")
    ):
        return "same_server_other_channel"
    return "cross_space"


def _serialize_memory_item(item: MemoryItem, scope: Scope) -> dict:
    row = {
        "kind": item.kind.value,
        "content": item.content,
        "confidence": item.confidence,
        "scope_relation": _scope_relation(item, scope),
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
        _serialize_memory_item(item, scope)
        for item in _owned(items, scope)
        if memory_access(item, scope) == MemoryAccess.FULL
    ]


def _full_shared_items(
    items: Iterable[MemoryItem],
    scope: Scope,
) -> list[MemoryItem]:
    """Return owner non-relationship items with FULL access in a shared space."""

    if scope.guild_id is None:
        return []
    return [
        item
        for item in _owned(items, scope)
        if item.kind != MemoryKind.RELATIONSHIP
        and memory_access(item, scope) == MemoryAccess.FULL
    ]


def full_shared_memory(items: Iterable[MemoryItem], scope: Scope) -> list[dict]:
    """Serialize non-relationship memory that is FULL in the current shared space."""

    return [
        _serialize_memory_item(item, scope)
        for item in _full_shared_items(items, scope)
    ]


def full_relationship_memory(items: Iterable[MemoryItem], scope: Scope) -> list[dict]:
    """Return bounded raw relationship items that are FULL in the current shared space."""

    if scope.guild_id is None:
        return []
    return [
        _serialize_memory_item(item, scope)
        for item in full_relationship_observations(items, scope)
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
        profile = aggregate_owner_relationship_evidence(items, scope)
        return {"items": selected, "relationship_axes": sorted(profile)}

    for item in _full_shared_items(items, scope):
        selected.append({
            "item_id": item.id,
            "projection": "shared_full",
            "kind": item.kind.value,
            "disclosure": item.disclosure.value,
            "origin_realm": item.origin_realm,
            "origin_channel_id": item.origin_channel_id,
            "access": MemoryAccess.FULL.value,
        })

    full_items = full_relationship_observations(items, scope)
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

    implicit_items = implicit_relationship_profile_contributors(items, scope)
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
        "structured_full_memory": [],
        "structured_relationship_memory": [],
        "owner_relationship_profile": {},
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
            **_serialize_memory_item(item, scope),
            "authorization": "owner_explicit_reference",
        }
        for item in items
        if item.id in authorized_ids
    ]
    return {
        "structured_owner_memory": owner_dm_memory(items, scope),
        "structured_full_memory": full_shared_memory(items, scope),
        "structured_relationship_memory": full_relationship_memory(items, scope),
        "owner_relationship_profile": (
            aggregate_owner_relationship_evidence(items, scope)
            if scope.guild_id is None
            else {}
        ),
        "cross_space_relationship": (
            aggregate_relationship_evidence(items, scope)
            if allow_cross_space
            else {}
        ),
        "authorized_factual_memory": authorized,
    }


def _hours_since(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace(" ", "T", 1))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return max(0.0, (now - moment).total_seconds() / 3600)


def personal_memory_selection(
    store,
    scope: Scope,
    *,
    message: str,
    settings,
    reply_texts: Iterable[str] = (),
    authorized_factual_items: Iterable[MemoryItem] = (),
    explicit_call: bool = True,
    now: datetime | None = None,
) -> tuple[MemorySelection, list[MemoryItem]] | None:
    """Run the tiered selector for the current owner; None when memory is unreadable."""

    reader = getattr(store, "memory_items", None)
    if not callable(reader):
        return None
    items = _owned(reader(scope.user_id), scope)
    annotation_reader = getattr(store, "memory_annotations", None)
    annotations = (
        annotation_reader([item.id for item in items])
        if callable(annotation_reader) and items
        else {}
    )
    zone = ZoneInfo(getattr(settings, "runtime_timezone", "") or "Asia/Seoul")
    now = (now or datetime.now(zone)).astimezone(zone)
    last_turn = getattr(store, "latest_turn_at", None)
    ctx = SelectionContext(
        scope=scope,
        message=message,
        now=now,
        reply_texts=tuple(text for text in reply_texts if text),
        hours_since_last_turn=_hours_since(last_turn(scope.user_id), now)
        if callable(last_turn) else None,
        session_followup=getattr(settings, "memory_session_followup", "dm"),
        explicit_call=explicit_call,
        authorized_item_ids=frozenset(
            item.id for item in authorized_factual_items if item.user_id == str(scope.user_id)
        ),
    )
    return select_personal_memory(items, annotations, ctx), items


def tiered_memory_context(
    scope: Scope,
    selection: MemorySelection,
    items: Iterable[MemoryItem],
    *,
    allow_cross_space: bool,
) -> tuple[dict, bool]:
    """Provider fields for the tiered read path and whether to attach its policy.

    The policy is attached whenever the owner has any memory, even when nothing was
    selected, so the instruction prefix stays stable for prompt caching (#449 §7 7).
    """

    items = list(items)
    profile = (
        aggregate_owner_relationship_evidence(items, scope)
        if scope.guild_id is None
        else aggregate_shared_relationship_evidence(
            items, scope, include_implicit=allow_cross_space
        )
    )
    has_memory = bool(items) or bool(profile)
    if not has_memory:
        return {}, False
    return {
        "user_memory": render_user_memory(selection, scope),
        "relationship_profile": profile,
    }, True


def tiered_memory_provenance(selection: MemorySelection, relationship_profile: dict) -> dict:
    """Content-free admitted items in the shape the trace view already reads."""

    items = []
    for entry in selection.selected:
        item = entry.item
        items.append({
            "store": "personal",
            "item_id": item.id,
            "projection": f"tiered_{entry.tier.lower()}_{entry.placement}",
            "tier": entry.tier,
            "status": entry.status.status.value,
            "kind": item.kind.value,
            "disclosure": item.disclosure.value,
            "origin_realm": item.origin_realm,
            "origin_channel_id": item.origin_channel_id,
            "access": MemoryAccess.FULL.value if entry.placement != "quiet_boundaries" else (
                "cross_space_boundary"
            ),
        })
    return {"items": items, "relationship_axes": sorted(relationship_profile)}


_READ_MODES = frozenset({"legacy", "shadow", "tiered"})
_REPLY_CHAIN_KINDS = frozenset({
    "reply_reference_source", "reply_origin_source", "reply_origin_request", "replied_message",
})


def reply_chain_texts(channel_context) -> list[str]:
    """Texts of the explicit reply chain, a stronger relevance signal than channel chatter."""

    return [
        str(row.get("content") or "")
        for row in channel_context or ()
        if isinstance(row, dict) and row.get("context_kind") in _REPLY_CHAIN_KINDS
    ]


def personal_memory_fields(
    store,
    scope: Scope,
    *,
    settings,
    use_memory: bool,
    allow_cross_space: bool,
    authorized_factual_items: Iterable[MemoryItem] = (),
    message: str = "",
    channel_context=(),
    explicit_call: bool = True,
    now: datetime | None = None,
) -> dict:
    """Provider fields, policy choice, selection and trace for PERSONAL_MEMORY_READ_MODE.

    ``legacy`` keeps the existing projection, ``shadow`` answers with it but also
    records the tiered selection, and ``tiered`` sends only the selected memory.
    """

    mode = getattr(settings, "personal_memory_read_mode", "legacy")
    mode = mode if mode in _READ_MODES else "legacy"
    authorized = tuple(authorized_factual_items)
    result = {"mode": mode, "selection": None, "policy": None}
    run = (
        personal_memory_selection(
            store, scope, message=message, settings=settings,
            reply_texts=reply_chain_texts(channel_context),
            authorized_factual_items=authorized, explicit_call=explicit_call, now=now,
        )
        if use_memory and mode != "legacy"
        else None
    )
    if mode == "tiered" and run is not None:
        selection, items = run
        fields, has_memory = tiered_memory_context(
            scope, selection, items, allow_cross_space=allow_cross_space
        )
        result.update(
            fields=fields,
            policy="tiered" if has_memory else None,
            selection=selection,
            trace=tiered_memory_provenance(selection, fields.get("relationship_profile", {})),
        )
        return result
    fields = structured_memory_context(
        store, scope, use_memory=use_memory, allow_cross_space=allow_cross_space,
        authorized_factual_items=authorized,
    )
    result.update(
        fields=fields,
        policy="legacy" if any(fields.values()) else None,
        selection=run[0] if run is not None else None,
        trace=structured_memory_provenance(
            store, scope, use_memory=use_memory, allow_cross_space=allow_cross_space,
            authorized_factual_items=authorized,
        ),
    )
    return result


__all__ = [
    "RELATIONSHIP_MAX_OBSERVATIONS",
    "RELATIONSHIP_MIN_ITEM_CONFIDENCE",
    "RELATIONSHIP_RECENCY_DECAY",
    "aggregate_owner_relationship_evidence",
    "aggregate_relationship_evidence",
    "full_relationship_memory",
    "full_shared_memory",
    "owner_dm_memory",
    "personal_memory_fields",
    "personal_memory_selection",
    "reply_chain_texts",
    "selection_provenance",
    "structured_memory_context",
    "structured_memory_provenance",
    "tiered_memory_context",
    "tiered_memory_provenance",
]
