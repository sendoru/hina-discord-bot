"""Shared chatlog mode storage, effective inheritance and legacy migration.

These helpers are domain operations used by Dashboard and Discord, independent
of slash-command registration and Discord.py.
"""

from __future__ import annotations

from .routing import Scope
from .scope_overrides import CHATLOG_CAPTURE_NOTE_PREFIX, resolve_scope_chain

_MIGRATION_MARKER = "config:chatlog_unified_v1"


def _capture_overrides(store) -> dict[str, str]:
    rows = store.db.execute(
        "SELECT scope,text FROM notes WHERE scope LIKE ? ORDER BY scope",
        (CHATLOG_CAPTURE_NOTE_PREFIX + "%",),
    ).fetchall()
    results = {}
    for row in rows:
        value = str(row["text"]).strip()
        if value in {"all", "direct"}:
            results[str(row["scope"])[len(CHATLOG_CAPTURE_NOTE_PREFIX):]] = value
    return results


def _parents(key: str) -> tuple[str, ...]:
    if key == "global":
        return ("global",)
    if key.startswith("guild:") and ":channel:" in key:
        return ("global", key.split(":channel:", 1)[0], key)
    return ("global", key)


def migrate_legacy_chatlog_settings(store) -> None:
    """One-time legacy on/off + capture migration, run on Store initialization.

    A single DB transaction prevents another reader from observing an in-between
    state. The marker is written with the converted rows for crash safety.
    """
    if store.note(_MIGRATION_MARKER) == "1":
        return
    logs = store.chat_log_mode_overrides()
    captures = _capture_overrides(store)
    combined = {}
    for key in set(logs) | set(captures):
        enabled = "on"
        capture = "all"
        for part in _parents(key):
            enabled = logs.get(part, enabled)
            capture = captures.get(part, capture)
        combined[key] = "off" if enabled == "off" else capture

    with store.db:
        store.db.execute("DELETE FROM chat_log_modes")
        store.db.execute(
            "DELETE FROM notes WHERE scope LIKE ?",
            (CHATLOG_CAPTURE_NOTE_PREFIX + "%",),
        )
        for key, mode in combined.items():
            store.db.execute(
                "INSERT INTO chat_log_modes(scope,mode) VALUES (?,?)",
                (key, "off" if mode == "off" else "on"),
            )
            store.db.execute(
                "INSERT INTO notes(scope,text) VALUES (?,?)",
                (CHATLOG_CAPTURE_NOTE_PREFIX + key, "all" if mode == "off" else mode),
            )
        store.db.execute(
            "INSERT OR REPLACE INTO notes(scope,text) VALUES (?,?)",
            (_MIGRATION_MARKER, "1"),
        )


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
    if mode is None:
        store.set_chat_log_mode_override(scope_key, None)
        store.set_note(CHATLOG_CAPTURE_NOTE_PREFIX + scope_key, "")
    else:
        store.set_chat_log_mode_override(scope_key, "off" if mode == "off" else "on")
        store.set_note(
            CHATLOG_CAPTURE_NOTE_PREFIX + scope_key,
            "all" if mode == "off" else mode,
        )
