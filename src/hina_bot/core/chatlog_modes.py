"""Shared chatlog mode storage and effective inheritance.

These helpers are domain operations used by Dashboard and Discord, independent
of slash-command registration and Discord.py.
"""

from __future__ import annotations

from .routing import Scope
from .scope_overrides import CHATLOG_CAPTURE_NOTE_PREFIX, resolve_scope_chain


def _capture_overrides(store) -> dict[str, str]:
    rows = store.settings.note_rows(CHATLOG_CAPTURE_NOTE_PREFIX)
    results = {}
    for row in rows:
        value = str(row["text"]).strip()
        if value in {"all", "direct"}:
            results[str(row["scope"])[len(CHATLOG_CAPTURE_NOTE_PREFIX):]] = value
    return results


def unified_chatlog_chain(store, scope: Scope) -> dict[str, str | None]:
    """Resolve the same global/server/channel policy used by Discord and Dashboard."""
    keys = ["global", scope.channel]
    if scope.guild_id is not None:
        keys.append(scope.realm)
    enabled = store.chat_log_mode_overrides()
    captures = _capture_overrides(store)
    combined = {}
    for key in keys:
        if enabled.get(key) == "off":
            combined[key] = "off"
        elif enabled.get(key) is not None or captures.get(key) is not None:
            combined[key] = captures.get(key) or "all"
    return resolve_scope_chain(combined, scope, default="all")


def set_unified_chatlog_mode(store, scope_key: str, mode: str | None) -> None:
    """Update both backing overrides of one unified all/direct/off mode."""
    if mode is not None and mode not in {"all", "direct", "off"}:
        raise ValueError("Invalid chat log mode")
    enabled = None if mode is None else "off" if mode == "off" else "on"
    capture = None if mode is None else "all" if mode == "off" else mode
    store.settings.replace_chatlog_override(scope_key, enabled=enabled, capture=capture)
