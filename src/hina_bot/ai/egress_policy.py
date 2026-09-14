"""Final privacy boundary for context leaving the bot process.

Capture/routing policies decide what local context is available.  This module is deliberately
later and stricter: it decides what may be serialized into an external model request.
"""

from __future__ import annotations

from copy import deepcopy

FULL = "full"
BOT_INTERACTIONS_ONLY = "bot_interactions_only"
POLICIES = frozenset({FULL, BOT_INTERACTIONS_ONLY})


def normalize_policy(value: str) -> str:
    policy = str(value).strip().lower()
    if policy not in POLICIES:
        allowed = ", ".join(sorted(POLICIES))
        raise ValueError(f"EXTERNAL_CONTEXT_POLICY는 {allowed} 중 하나여야 합니다.")
    return policy


def strict_policy(value: str) -> bool:
    return normalize_policy(value) == BOT_INTERACTIONS_ONLY


def _author_id(row: dict) -> str:
    return str(row.get("author_user_id") or row.get("user_id") or "")


def allow_channel_row(row: dict, current_user_id: int | str, policy: str) -> bool:
    """Return whether one recent/reply row may leave the process.

    Strict mode is fail-closed.  Any user's row needs explicit direct-call provenance, except for
    the caller's own message that they explicitly selected as the current Discord reply target.
    Hina's assistant rows are part of the shared bot conversation in this channel.
    """
    if normalize_policy(policy) == FULL:
        return True

    current = str(current_user_id)
    kind = str(row.get("context_kind") or "")
    role = str(row.get("role") or "")

    if kind == "target_user_history":
        return False
    if role == "assistant":
        return True
    if role != "user":
        return False
    if kind == "replied_message":
        # Explicit replies do not turn arbitrary third-party chatter into bot conversation.
        return _author_id(row) == current
    return row.get("direct_trigger") is True


def filter_channel_context(
    rows: list[dict] | tuple[dict, ...] | None,
    current_user_id: int | str,
    policy: str,
) -> list[dict]:
    values = list(rows or [])
    if normalize_policy(policy) == FULL:
        return values
    return [row for row in values if allow_channel_row(row, current_user_id, policy)]


def filter_public_context(
    rows: list[dict] | tuple[dict, ...] | None,
    current_user_id: int | str,
    policy: str,
) -> list[dict]:
    values = list(rows or [])
    if normalize_policy(policy) == FULL:
        return values
    current = str(current_user_id)
    return [row for row in values if str(row.get("user_id") or "") == current]


def apply_context_policy(context: dict, current_user_id: int | str, policy: str) -> dict:
    """Filter the complete model-reference object immediately before serialization."""
    normalized = normalize_policy(policy)
    if normalized == FULL:
        return context

    filtered = deepcopy(context)
    filtered["server_note"] = ""
    filtered["channel_recent_messages"] = filter_channel_context(
        filtered.get("channel_recent_messages"), current_user_id, normalized
    )
    filtered["public_server_context"] = filter_public_context(
        filtered.get("public_server_context"), current_user_id, normalized
    )
    return filtered


__all__ = [
    "BOT_INTERACTIONS_ONLY",
    "FULL",
    "POLICIES",
    "allow_channel_row",
    "apply_context_policy",
    "filter_channel_context",
    "filter_public_context",
    "normalize_policy",
    "strict_policy",
]
