"""Content-free provenance snapshots for the final model context."""

from collections.abc import Iterable


_MAX_SOURCES = 96
_MAX_STRUCTURED_ITEMS = 96

_REFERENCE_KINDS = {
    "replied_message",
    "reply_reference_source",
    "reply_origin_source",
    "prior_reply_source",
    "target_user_history",
}


def _owner_relation(row: dict, current_user_id: int | str) -> str:
    role = str(row.get("role") or "")
    if role == "assistant":
        return "assistant"
    author = str(row.get("author_user_id") or row.get("user_id") or "")
    if not author:
        return "unknown"
    return "self" if author == str(current_user_id) else "other"


def _message_source(row: dict, *, source_type: str, current_user_id: int | str) -> dict:
    kind = str(row.get("context_kind") or "")
    provenance = str(row.get("provenance_class") or "")
    result = {
        "source_type": source_type,
        "context_kind": kind or "unspecified",
        "role": str(row.get("role") or "unknown"),
        "owner_relation": _owner_relation(row, current_user_id),
        "access": "full",
        "is_reference": kind in _REFERENCE_KINDS
        or provenance in {"reference_material", "reference_derived"},
    }
    optional = (
        "message_id",
        "author_user_id",
        "author_name",
        "name",
        "reply_target_user_id",
        "provenance_class",
        "reference_strength",
        "source_turn_message_id",
        "target_retrieval_mode",
    )
    for key in optional:
        value = row.get(key)
        if value not in (None, "", (), []):
            result[key] = str(value)[:160]
    if "author_user_id" not in result and row.get("user_id") not in (None, ""):
        result["author_user_id"] = str(row.get("user_id"))[:160]
    for key in ("direct_trigger", "explicit_history_request", "truncated"):
        if key in row and row.get(key) is not None:
            result[key] = bool(row.get(key))
    if row.get("has_visual"):
        result["has_visual"] = True
    return result


def _public_source(row: dict, current_user_id: int | str) -> dict:
    user_id = str(row.get("user_id") or "")
    result = {
        "source_type": "public_server_context",
        "owner_relation": (
            "self" if user_id and user_id == str(current_user_id)
            else "other" if user_id
            else "unknown"
        ),
        "access": "full",
        "is_reference": False,
    }
    for key in ("source", "user_id", "name"):
        value = row.get(key)
        if value not in (None, ""):
            result[key] = str(value)[:200]
    return result


def _lore_source(row: dict) -> dict:
    result = {
        "source_type": "lore_reference",
        "access": "full",
        "is_reference": True,
    }
    reference_id = row.get("reference") or row.get("id")
    if reference_id not in (None, ""):
        result["reference_id"] = str(reference_id)[:240]
    for key in ("kind", "awareness", "lane", "locator"):
        value = row.get(key)
        if value not in (None, ""):
            result[key] = str(value)[:240]
    if row.get("source_type") not in (None, ""):
        result["lore_source_type"] = str(row.get("source_type"))[:120]
    return result


def _visual_source(visual) -> dict:
    return {
        "source_type": "visual_input",
        "source": str(getattr(visual, "source", "") or "")[:80],
        "context_kind": str(getattr(visual, "context_kind", "") or "")[:80],
        "reference_strength": str(getattr(visual, "reference_strength", "") or "")[:80],
        "message_id": str(getattr(visual, "message_id", "") or "")[:160],
        "author_user_id": str(getattr(visual, "author_user_id", "") or "")[:160],
        "author_name": str(getattr(visual, "author_name", "") or "")[:100],
        "mime_type": str(getattr(visual, "mime_type", "") or "")[:80],
        "access": "full",
        "is_reference": getattr(visual, "context_kind", "") != "current_message",
    }


def _section(name: str, value, *, blocked: int = 0) -> dict:
    if isinstance(value, (dict, list, tuple)):
        count = len(value)
        included = bool(value)
    else:
        count = int(bool(value))
        included = bool(value)
    return {
        "name": name,
        "included": included,
        "count": count,
        "blocked_count": max(0, int(blocked)),
    }


def build_context_provenance(
    context: dict,
    scope,
    *,
    egress_policy: str,
    adapter_egress: dict | None,
    provider_boundary: dict | None,
    use_memory: bool,
    current_channel_only: bool,
    cross_channel_memory: bool,
    structured: dict | None = None,
    visuals: Iterable | None = None,
    conversation_history_message_ids: Iterable[str] | None = None,
) -> dict:
    """Build a bounded, content-free description of context admitted to the answer request."""

    current_user_id = scope.user_id
    active_reply = list(context.get("active_reply_chain", ()) or ())
    channel = list(context.get("channel_recent_messages", ()) or ())
    public = list(context.get("public_server_context", ()) or ())
    lore = list(context.get("lore_reference", ()) or ())
    visual_rows = list(visuals or ())
    history_message_ids = [
        str(value)
        for value in conversation_history_message_ids or ()
        if str(value)
    ]
    personal_recent = list(context.get("personal_recent_conversation", ()) or ())
    structured = dict(structured or {})
    all_structured_items = list(structured.get("items", ()) or ())
    structured_items = all_structured_items[-_MAX_STRUCTURED_ITEMS:]
    relationship_axes = list(structured.get("relationship_axes", ()) or ())

    adapter = dict(adapter_egress or {})
    provider = dict(provider_boundary or {})
    sections = [
        _section("server_note", context.get("server_note")),
        _section("user_note", context.get("user_note")),
        _section("conversation_memory", context.get("conversation_memory")),
        _section("personal_recent_conversation", context.get("personal_recent_conversation", ())),
        _section("conversation_history", context.get("conversation_history", ())),
        _section(
            "public_server_context",
            public,
            blocked=provider.get("public_blocked", 0),
        ),
        _section(
            "channel_recent_messages",
            channel,
            blocked=provider.get("channel_blocked", 0),
        ),
        _section("active_reply_chain", active_reply),
        _section("structured_memory", all_structured_items),
        _section("relationship_projection", relationship_axes),
        _section("lore_reference", lore),
        _section("visual_inputs", visual_rows),
    ]

    all_sources = [
        *(
            _message_source(
                row,
                source_type="active_reply_chain",
                current_user_id=current_user_id,
            )
            for row in active_reply
        ),
        *(
            _message_source(
                row,
                source_type="channel_recent_messages",
                current_user_id=current_user_id,
            )
            for row in channel
        ),
        *(
            {
                "source_type": "personal_recent_conversation",
                "message_id": str(row.get("message_id") or "")[:160],
                "owner_relation": "self",
                "access": "full",
                "is_reference": False,
            }
            for row in personal_recent
        ),
        *(
            {
                "source_type": "conversation_history",
                "message_id": message_id[:160],
                "owner_relation": "self",
                "access": "full",
                "is_reference": False,
            }
            for message_id in history_message_ids
        ),
        *(_public_source(row, current_user_id) for row in public),
        *(_lore_source(row) for row in lore),
        *(_visual_source(visual) for visual in visual_rows),
    ]
    sources = all_sources[-_MAX_SOURCES:]

    return {
        "version": 1,
        "scope": "guild" if scope.guild_id is not None else "dm",
        "current_user_id": str(scope.user_id),
        "egress_policy": str(egress_policy),
        "decisions": {
            "use_memory": bool(use_memory),
            "current_channel_only": bool(current_channel_only),
            "cross_channel_memory": bool(cross_channel_memory),
        },
        "egress": {
            "adapter": adapter,
            "provider_boundary": provider,
        },
        "sections": sections,
        "sources": sources,
        "structured_memory": structured_items,
        "relationship_axes": relationship_axes,
        "truncated": {
            "sources": max(0, len(all_sources) - len(sources)),
            "structured_memory": max(
                0,
                len(all_structured_items) - len(structured_items),
            ),
        },
    }


__all__ = ["build_context_provenance"]
