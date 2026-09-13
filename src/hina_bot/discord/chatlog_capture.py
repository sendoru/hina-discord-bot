"""Scope-aware policy for which channel messages enter recent context."""

from .routing import Scope

_PREFIX = "config:chatlog_capture:"


def _key(scope_key: str) -> str:
    return _PREFIX + scope_key


def capture_mode_override(store, scope_key: str) -> str | None:
    value = store.note(_key(scope_key)).strip()
    return value if value in {"all", "direct"} else None


def capture_mode_chain(store, scope: Scope) -> dict[str, str | None]:
    global_mode = capture_mode_override(store, "global")
    server_mode = capture_mode_override(store, scope.realm) if scope.guild_id is not None else None
    channel_mode = capture_mode_override(store, scope.channel)
    if channel_mode is not None:
        effective, source = channel_mode, "channel"
    elif server_mode is not None:
        effective, source = server_mode, "server"
    elif global_mode is not None:
        effective, source = global_mode, "global"
    else:
        effective, source = "all", "default"
    return {
        "global": global_mode,
        "server": server_mode,
        "channel": channel_mode,
        "effective": effective,
        "source": source,
    }


def capture_mode(store, scope: Scope) -> str:
    return str(capture_mode_chain(store, scope)["effective"])


def set_capture_mode_override(store, scope_key: str, mode: str | None) -> None:
    if mode is not None and mode not in {"all", "direct"}:
        raise ValueError("Invalid chat log capture mode")
    store.set_note(_key(scope_key), mode or "")
