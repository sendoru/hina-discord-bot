"""Shared helpers for global/server/channel runtime overrides."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

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


def scope_target_key(scope: Scope, target: str) -> str:
    """Resolve a global/server/channel target identically for Discord and Dashboard."""
    if target == "global":
        return "global"
    if target == "server":
        if scope.guild_id is None:
            raise ValueError("DM에서는 서버 설정을 변경할 수 없어요.")
        return scope.realm
    if target == "channel":
        return scope.channel
    raise ValueError("알 수 없는 설정 범위예요.")


class MemoryMode(str, Enum):
    """Effective automatic-memory policy, also used by the bot's runtime."""

    normal = "normal"
    read_only = "read_only"
    write_only = "write_only"
    off = "off"

    @property
    def reads(self) -> bool:
        return self in (MemoryMode.normal, MemoryMode.read_only)

    @property
    def writes(self) -> bool:
        return self in (MemoryMode.normal, MemoryMode.write_only)


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
    "MemoryMode",
    "effective_recent_context_mode",
    "memory_mode_capabilities",
    "resolve_scope_chain",
    "scope_target_key",
]
