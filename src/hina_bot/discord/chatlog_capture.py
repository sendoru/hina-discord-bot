"""Scope-aware policy for which channel messages enter recent context."""

from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import (
    CHATLOG_CAPTURE_NOTE_PREFIX,
    resolve_scope_chain,
)

_PREFIX = CHATLOG_CAPTURE_NOTE_PREFIX


def _key(scope_key: str) -> str:
    return _PREFIX + scope_key


def capture_mode_override(store, scope_key: str) -> str | None:
    value = store.note(_key(scope_key)).strip()
    return value if value in {"all", "direct"} else None


def capture_mode_overrides(store) -> dict[str, str]:
    rows = store.db.execute(
        "SELECT scope,text FROM notes WHERE scope LIKE ? ORDER BY scope",
        (_PREFIX + "%",),
    ).fetchall()
    result = {}
    for row in rows:
        value = str(row["text"]).strip()
        if value in {"all", "direct"}:
            result[str(row["scope"])[len(_PREFIX):]] = value
    return result


def capture_mode_chain(store, scope: Scope) -> dict[str, str | None]:
    return resolve_scope_chain(
        capture_mode_overrides(store),
        scope,
        default="all",
    )


def capture_mode(store, scope: Scope) -> str:
    return str(capture_mode_chain(store, scope)["effective"])


def set_capture_mode_override(store, scope_key: str, mode: str | None) -> None:
    if mode is not None and mode not in {"all", "direct"}:
        raise ValueError("Invalid chat log capture mode")
    store.set_note(_key(scope_key), mode or "")
