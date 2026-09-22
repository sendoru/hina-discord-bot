"""Bounded causal context snapshots for personal long-term memory."""

from __future__ import annotations

import json
from contextvars import ContextVar

CURRENT_MEMORY_CONTEXT: ContextVar[tuple[dict, ...]] = ContextVar(
    "current_memory_context",
    default=(),
)
CURRENT_CONTEXT_PROVENANCE: ContextVar[dict | None] = ContextVar(
    "current_context_provenance",
    default=None,
)
CURRENT_EGRESS_DECISION: ContextVar[dict | None] = ContextVar(
    "current_egress_decision",
    default=None,
)

_MEMORY_CONTEXT_KINDS = (
    "replied_message",
    "reply_origin_request",
    "reply_reference_source",
    "reply_origin_source",
)
_MEMORY_CONTEXT_MAX_ITEMS = 3
_MEMORY_CONTEXT_MAX_CHARS = 1600


def _ownership(row: dict, current_user_id: int) -> str:
    role = str(row.get("role", "user"))
    if role == "assistant":
        return "assistant"
    author_id = str(row.get("author_user_id") or row.get("user_id") or "")
    return "self" if author_id == str(current_user_id) else "external"


def build_memory_context(
    rows,
    current_user_id: int,
    *,
    max_items: int = _MEMORY_CONTEXT_MAX_ITEMS,
    max_chars: int = _MEMORY_CONTEXT_MAX_CHARS,
) -> list[dict]:
    """Keep only the strongest causal text needed to interpret the current user turn."""
    by_kind: dict[str, list[dict]] = {kind: [] for kind in _MEMORY_CONTEXT_KINDS}
    for row in rows or ():
        kind = str(row.get("context_kind", ""))
        if kind not in by_kind:
            continue
        content = str(row.get("content", "")).strip()
        if not content:
            continue
        by_kind[kind].append(dict(row))

    selected: list[dict] = []
    remaining = max(0, int(max_chars))
    for kind in _MEMORY_CONTEXT_KINDS:
        for row in reversed(by_kind[kind]):
            if len(selected) >= max_items or remaining <= 0:
                return selected
            content = str(row.get("content", "")).strip()
            clipped = content[:remaining]
            if not clipped:
                continue
            item = {
                "kind": kind,
                "role": str(row.get("role", "user")),
                "ownership": _ownership(row, current_user_id),
                "content": clipped,
            }
            message_id = str(row.get("message_id") or "")
            if message_id:
                item["message_id"] = message_id
            author_id = str(row.get("author_user_id") or row.get("user_id") or "")
            if author_id:
                item["author_user_id"] = author_id
            author_name = str(row.get("author_name") or row.get("name") or "").strip()
            if author_name:
                item["author_name"] = author_name[:100]
            provenance_class = str(row.get("provenance_class") or "")
            if provenance_class:
                item["provenance_class"] = provenance_class
            source_turn_message_id = str(row.get("source_turn_message_id") or "")
            if source_turn_message_id:
                item["source_turn_message_id"] = source_turn_message_id
            selected.append(item)
            remaining -= len(clipped)
    return selected


def decode_memory_context(value: str) -> list[dict]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


__all__ = [
    "CURRENT_CONTEXT_PROVENANCE",
    "CURRENT_EGRESS_DECISION",
    "CURRENT_MEMORY_CONTEXT",
    "build_memory_context",
    "decode_memory_context",
]
