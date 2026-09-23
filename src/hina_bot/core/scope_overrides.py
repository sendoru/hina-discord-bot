"""Shared helpers for global/server/channel runtime overrides."""

from __future__ import annotations

from collections.abc import Mapping

from .routing import Scope

CHATLOG_CAPTURE_NOTE_PREFIX = "config:chatlog_capture:"


def resolve_scope_chain(
    overrides: Mapping[str, str],
    scope: Scope,
    *,
    default: str,
) -> dict[str, str | None]:
    """Resolve global -> server -> channel inheritance for one runtime setting."""

    global_value = overrides.get("global")
    server_value = overrides.get(scope.realm) if scope.guild_id is not None else None
    channel_value = overrides.get(scope.channel)
    if channel_value is not None:
        effective, source = channel_value, "channel"
    elif server_value is not None:
        effective, source = server_value, "server"
    elif global_value is not None:
        effective, source = global_value, "global"
    else:
        effective, source = default, "default"
    return {
        "global": global_value,
        "server": server_value,
        "channel": channel_value,
        "effective": effective,
        "source": source,
    }


def memory_mode_capabilities(mode: str) -> tuple[bool, bool]:
    """Return (reads, writes) for one effective automatic-memory mode."""

    mapping = {
        "normal": (True, True),
        "read_only": (True, False),
        "write_only": (False, True),
        "off": (False, False),
    }
    try:
        return mapping[str(mode)]
    except KeyError as exc:
        raise ValueError(f"Unknown memory mode: {mode}") from exc


def effective_recent_context_mode(
    scope: Scope,
    *,
    chat_log_mode: str,
    capture_mode: str,
) -> str:
    """Return the actual recent-channel-context behavior for a scope."""

    if scope.guild_id is None:
        return "off"
    if chat_log_mode == "off":
        return "off"
    if capture_mode not in {"all", "direct"}:
        raise ValueError(f"Unknown chat-log capture mode: {capture_mode}")
    return capture_mode


__all__ = [
    "CHATLOG_CAPTURE_NOTE_PREFIX",
    "effective_recent_context_mode",
    "memory_mode_capabilities",
    "resolve_scope_chain",
]
