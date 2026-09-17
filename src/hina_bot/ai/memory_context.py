"""Bounded causal context snapshots for personal long-term memory."""

from __future__ import annotations

import json

_MEMORY_CONTEXT_KINDS = (
    "replied_message",
    "reply_origin_request",
    "reply_origin_source",
)
_MEMORY_CONTEXT_MAX_ITEMS = 2
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
            selected.append({
                "kind": kind,
                "role": str(row.get("role", "user")),
                "ownership": _ownership(row, current_user_id),
                "content": clipped,
            })
            remaining -= len(clipped)
    return selected


def encode_memory_context(context) -> str:
    if not context:
        return ""
    return json.dumps(list(context), ensure_ascii=False, separators=(",", ":"))


def decode_memory_context(value: str) -> list[dict]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


__all__ = [
    "build_memory_context",
    "decode_memory_context",
    "encode_memory_context",
]
