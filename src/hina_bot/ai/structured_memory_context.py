"""Build privacy-bounded structured-memory context for model responses."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hina_bot.core.config import PERSONAL_MEMORY_READ_MODES, canonical_memory_read_mode
from hina_bot.core.memory_annotations import MemoryAnnotation
from hina_bot.core.memory_items import (
    MemoryAccess,
    MemoryItem,
    MemoryKind,
    memory_access,
    same_disclosure_space,
)
from hina_bot.core.memory_selection import (
    MemoryReadRun,
    MemorySelection,
    SelectionContext,
    decision_memory_candidates,
    effective_annotation,
    effective_status,
    full_memory_items,
    selection_provenance,
)
from hina_bot.core.relationship_profile import (
    RELATIONSHIP_MAX_OBSERVATIONS,
    RELATIONSHIP_MIN_ITEM_CONFIDENCE,
    RELATIONSHIP_RECENCY_DECAY,
    aggregate_owner_relationship_evidence,
    aggregate_relationship_evidence,
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


def _semantic_annotation(
    item: MemoryItem,
    annotations: Mapping[int, MemoryAnnotation],
    now: datetime,
) -> dict:
    """Response-use judgments only; omit extraction evidence and internal bookkeeping.

    Missing/stale annotations use the same conservative fallback as memory selection.
    A stale directive may remain visible for safety, but is never presented as verified.
    """
    annotation, state = effective_annotation(item, annotations.get(item.id))
    status = effective_status(annotation, now)
    result = {
        "state": state,
        "time_scope": annotation.time_scope.value,
        "usage_mode": annotation.usage_mode.value,
        "audience": annotation.audience.value,
        "importance": annotation.importance,
        "explicit": annotation.explicit,
        "verified": annotation.verified and state == "current",
        "status": status.status.value,
    }
    if annotation.stability is not None:
        result["stability"] = annotation.stability.value
    if annotation.directive_type is not None:
        result["directive_type"] = annotation.directive_type.value
    if annotation.event_at:
        result["event_at"] = annotation.event_at
    if annotation.recurrence:
        result["recurrence"] = annotation.recurrence
    if status.date:
        result["status_date"] = status.date
    if status.noted:
        result["status_noted"] = status.noted
    if status.expired:
        result["expired"] = True
    return result


def _serialize_memory_item(
    item: MemoryItem,
    scope: Scope,
    *,
    annotations: Mapping[int, MemoryAnnotation] | None = None,
    now: datetime | None = None,
) -> dict:
    row = {
        "kind": item.kind.value,
        "content": item.content,
        "confidence": item.confidence,
        "scope_relation": _scope_relation(item, scope),
        "disclosure": item.disclosure.value,
    }
    if item.kind != MemoryKind.RELATIONSHIP:
        row["annotation"] = _semantic_annotation(
            item,
            annotations or {},
            now or datetime.now(ZoneInfo("Asia/Seoul")),
        )
    evidence = item.relationship_evidence.as_dict()
    if evidence:
        row["relationship_evidence"] = evidence
    return row


def owner_dm_memory(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    annotations: Mapping[int, MemoryAnnotation] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Serialize all structured memory owned by the current user only in their DM."""

    if scope.guild_id is not None:
        return []
    return [
        _serialize_memory_item(item, scope, annotations=annotations, now=now)
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


def full_shared_memory(
    items: Iterable[MemoryItem],
    scope: Scope,
    *,
    annotations: Mapping[int, MemoryAnnotation] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Serialize non-relationship memory that is FULL in the current shared space."""

    return [
        _serialize_memory_item(item, scope, annotations=annotations, now=now)
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
    items: Iterable[MemoryItem] | None = None,
) -> dict:
    """Return content-free provenance for the structured memory admitted to a response."""

    if not use_memory:
        return {"items": [], "relationship_axes": []}
    if items is None:
        reader = getattr(store, "memory_items", None)
        if not callable(reader):
            return {"items": [], "relationship_axes": []}

        items = _owned(reader(scope.user_id), scope)
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
    items: Iterable[MemoryItem] | None = None,
    annotations: Mapping[int, MemoryAnnotation] | None = None,
    now: datetime | None = None,
) -> dict:
    """Build structured fields safe to serialize into the response request."""

    empty = {
        "structured_owner_memory": [],
        "structured_full_memory": [],
        "structured_relationship_memory": [],
        "owner_relationship_profile": {},
        "cross_space_relationship": {},
    }
    if not use_memory:
        return empty

    if items is None:
        reader = getattr(store, "memory_items", None)
        if not callable(reader):
            return empty

        items = reader(scope.user_id)
    items = list(items)
    if annotations is None:
        annotation_reader = getattr(store, "memory_annotations", None)
        annotations = (
            annotation_reader([item.id for item in items if item.kind != MemoryKind.RELATIONSHIP])
            if callable(annotation_reader)
            else {}
        )
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    return {
        "structured_owner_memory": owner_dm_memory(
            items, scope, annotations=annotations, now=now,
        ),
        "structured_full_memory": full_shared_memory(
            items, scope, annotations=annotations, now=now,
        ),
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


# The user's own turns with Hina in this conversation that still count as the same topic.
RECENT_OWN_WINDOW = timedelta(minutes=30)
RECENT_OWN_TURNS = 2


def recent_own_texts(store, scope: Scope, now: datetime) -> tuple[str, ...]:
    """The current speaker's last few messages to Hina here, oldest first.

    ``store.history(scope)`` holds only this user's turns in this channel or DM, so
    other members' messages and Hina's replies never count as the user's topic.
    """

    reader = getattr(store, "history", None)
    if not callable(reader):
        return ()
    texts: list[str] = []
    for turn in reversed(reader(scope)):
        hours = _hours_since(turn["created_at"], now)
        if hours is None or hours * 3600 > RECENT_OWN_WINDOW.total_seconds():
            break
        content = str(turn["content"] or "").strip()
        if content:
            texts.append(content)
        if len(texts) >= RECENT_OWN_TURNS:
            break
    return tuple(reversed(texts))


@dataclass(frozen=True)
class PersonalMemorySelection:
    selection: MemorySelection | None
    items: list[MemoryItem]
    annotations: dict[int, MemoryAnnotation]
    ctx: SelectionContext
    recent_own_texts: tuple[str, ...] = ()


def personal_memory_selection(
    store,
    scope: Scope,
    *,
    message: str,
    settings,
    now: datetime | None = None,
    include_recent: bool = False,
) -> PersonalMemorySelection | None:
    """Run the tiered selector for the current owner; None when memory is unreadable."""

    reader = getattr(store, "memory_items", None)
    if not callable(reader):
        return None
    items = _owned(reader(scope.user_id), scope)
    if scope.guild_id is not None and getattr(settings, "personal_memory_cross_scope", "on") == "off":
        opt_in = getattr(store, "cross_scope_opt_in", None)
        if not callable(opt_in) or not opt_in(scope.user_id):
            items = [item for item in items if same_disclosure_space(item, scope)]
    annotation_reader = getattr(store, "memory_annotations", None)
    annotations = (
        annotation_reader([item.id for item in items])
        if callable(annotation_reader) and items
        else {}
    )
    zone = ZoneInfo(getattr(settings, "runtime_timezone", "") or "Asia/Seoul")
    now = (now or datetime.now(zone)).astimezone(zone)
    own_texts = recent_own_texts(store, scope, now) if include_recent else ()
    ctx = SelectionContext(scope=scope, message=message, now=now)
    eligible = full_memory_items(items, annotations, ctx)
    if scope.guild_id is not None:
        raw_relationship_ids = {item.id for item in full_relationship_observations(eligible, scope)}
        eligible = [
            item for item in eligible if item.kind != MemoryKind.RELATIONSHIP
            or memory_access(item, scope) == MemoryAccess.IMPLICIT or item.id in raw_relationship_ids
        ]
    return PersonalMemorySelection(None, eligible, annotations, ctx, own_texts)


REPLY_CHAIN_KINDS = frozenset({
    "reply_reference_source", "reply_origin_source", "reply_origin_request", "replied_message",
})


def personal_memory_fields(
    store,
    scope: Scope,
    *,
    settings,
    use_memory: bool,
    allow_cross_space: bool,
    message: str = "",
    now: datetime | None = None,
    read_mode: str | None = None,
    decision_run: PersonalMemorySelection | None = None,
    decision_scores: dict[str, float] | None = None,
) -> dict:
    """Provider fields, policy choice, selection and trace for PERSONAL_MEMORY_READ_MODE.

    A successful Decision selection is retained for post-reply usage accounting.
    """

    mode = read_mode or getattr(settings, "personal_memory_read_mode", "legacy")
    mode = canonical_memory_read_mode(mode)
    mode = mode if mode in PERSONAL_MEMORY_READ_MODES else "full"
    result = {"mode": mode, "selection": None, "policy": None, "run": None}
    run = None
    if decision_run is not None and decision_scores is not None:
        run = decision_run
        selection = MemorySelection(selected=[
            replace(entry, via="decision")
            for entry in decision_memory_candidates(run.items, run.annotations, run.ctx)
            if str(entry.item.id) in decision_scores
        ])
        run = PersonalMemorySelection(
            selection, run.items, run.annotations, run.ctx, run.recent_own_texts,
        )
    if run is not None and run.selection is not None:
        result["run"] = MemoryReadRun(
            mode=mode,
            selection=run.selection,
        )
    snapshot = decision_run
    if use_memory and snapshot is None:
        snapshot = personal_memory_selection(
            store, scope, message=message, settings=settings, now=now,
        )
    items = snapshot.items if snapshot else ()
    if mode == "decision" and decision_scores is not None:
        items = [item for item in items if str(item.id) in decision_scores
                 or memory_access(item, scope) == MemoryAccess.IMPLICIT]
    fields = structured_memory_context(
        store, scope, use_memory=use_memory, allow_cross_space=allow_cross_space,
        items=items,
        annotations=snapshot.annotations if snapshot else None,
        now=snapshot.ctx.now if snapshot else now,
    )
    result.update(
        fields=fields,
        policy="legacy" if any(fields.values()) else None,
        selection=run.selection if run is not None else None,
        trace=structured_memory_provenance(
            store, scope, use_memory=use_memory, allow_cross_space=allow_cross_space,
            items=items,
        ),
    )
    return result


__all__ = [
    "RELATIONSHIP_MAX_OBSERVATIONS",
    "RELATIONSHIP_MIN_ITEM_CONFIDENCE",
    "RELATIONSHIP_RECENCY_DECAY",
    "PersonalMemorySelection",
    "aggregate_owner_relationship_evidence",
    "aggregate_relationship_evidence",
    "full_relationship_memory",
    "full_shared_memory",
    "owner_dm_memory",
    "personal_memory_fields",
    "personal_memory_selection",
    "recent_own_texts",
    "selection_provenance",
    "structured_memory_context",
    "structured_memory_provenance",
]
