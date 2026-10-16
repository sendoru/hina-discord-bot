"""Settings persistence on the connection owned and closed by Store.

Construction performs no SQL. Each write keeps its existing transaction boundary;
callers must not wrap unrelated settings writes in a memory extraction batch.
"""

import sqlite3

from .settings_schema import (
    SETTINGS_DDL,
    migrate_legacy_rag_override,
    replace_legacy_chatlog_settings,
)


class SettingsRepository:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def note(self, key: str) -> str:
        row = self.db.execute("SELECT text FROM notes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else ""

    def set_note(self, key: str, text: str):
        with self.db:
            if text:
                self.db.execute("INSERT OR REPLACE INTO notes VALUES (?,?)", (key, text[:1500]))
            else:
                self.db.execute("DELETE FROM notes WHERE scope=?", (key,))

    def emoji_rows(self):
        return self.db.execute("SELECT * FROM emoji_registry ORDER BY alias").fetchall()

    def add_emoji(self, alias, emoji_id, description, source_guild_id=None):
        with self.db:
            self.db.execute("INSERT INTO emoji_registry VALUES (?,?,?,?)",
                            (alias, emoji_id, description, source_guild_id))

    def edit_emoji(self, alias, description):
        with self.db:
            return self.db.execute("UPDATE emoji_registry SET description=? WHERE alias=?",
                                   (description, alias)).rowcount > 0

    def remove_emoji(self, alias):
        with self.db:
            return self.db.execute("DELETE FROM emoji_registry WHERE alias=?", (alias,)).rowcount > 0

    def memory_mode_override(self, key: str) -> str | None:
        row = self.db.execute("SELECT mode FROM memory_modes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else None

    def set_memory_mode_override(self, key: str, mode: str | None):
        if mode is not None and mode not in {"normal", "read_only", "write_only", "off"}:
            raise ValueError("Invalid memory mode")
        with self.db:
            if mode is None:
                self.db.execute("DELETE FROM memory_modes WHERE scope=?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO memory_modes VALUES (?,?)", (key, mode))

    def memory_mode_overrides(self) -> dict[str, str]:
        rows = self.db.execute("SELECT scope,mode FROM memory_modes ORDER BY scope").fetchall()
        return {row["scope"]: row["mode"] for row in rows}

    def chat_log_mode_override(self, key: str) -> str | None:
        row = self.db.execute("SELECT mode FROM chat_log_modes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else None

    def set_chat_log_mode_override(self, key: str, mode: str | None):
        if mode is not None and mode not in {"on", "off"}:
            raise ValueError("Invalid chat log mode")
        with self.db:
            if mode is None:
                self.db.execute("DELETE FROM chat_log_modes WHERE scope=?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO chat_log_modes VALUES (?,?)", (key, mode))

    def chat_log_mode_overrides(self) -> dict[str, str]:
        rows = self.db.execute("SELECT scope,mode FROM chat_log_modes ORDER BY scope").fetchall()
        return {row["scope"]: row["mode"] for row in rows}

    def note_rows(self, prefix: str):
        return self.db.execute(
            "SELECT scope,text FROM notes WHERE scope LIKE ? ORDER BY scope",
            (prefix + "%",),
        ).fetchall()

    def replace_legacy_chatlog_settings(self, combined: dict[str, str], marker: str) -> None:
        with self.db:
            replace_legacy_chatlog_settings(self.db, combined, marker)

    def ensure_runtime_schema(self) -> None:
        """Explicit initialization; caller owns the transaction."""
        for statement in SETTINGS_DDL:
            self.db.execute(statement)

    def migrate_legacy_rag_override(self, encoded_modes: dict[str, str]) -> None:
        with self.db:
            migrate_legacy_rag_override(self.db, encoded_modes)

    def replace_startup_snapshot(self, rows: list[tuple[str, str]]) -> None:
        with self.db:
            self.db.execute("DELETE FROM runtime_config_startup")
            self.db.executemany(
                """INSERT INTO runtime_config_startup(key,value,captured_at)
                   VALUES (?,?,CURRENT_TIMESTAMP)""",
                rows,
            )

    def runtime_values(self) -> dict[str, str]:
        rows = self.db.execute(
            "SELECT key,value FROM runtime_config ORDER BY key"
        ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def set_runtime_value(self, attr: str, encoded: str | None) -> None:
        with self.db:
            if encoded is None:
                self.db.execute("DELETE FROM runtime_config WHERE key=?", (attr,))
            else:
                self.db.execute(
                    """INSERT INTO runtime_config(key,value,updated_at)
                       VALUES (?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(key) DO UPDATE SET
                           value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
                    (attr, encoded),
                )
