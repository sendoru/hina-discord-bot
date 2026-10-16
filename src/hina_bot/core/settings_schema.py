"""Explicit schema statements; caller owns the transaction."""

import json
import sqlite3

from .scope_overrides import CHATLOG_CAPTURE_NOTE_PREFIX

SETTINGS_DDL = (
    """CREATE TABLE IF NOT EXISTS runtime_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
    """CREATE TABLE IF NOT EXISTS runtime_config_startup (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
)


def replace_legacy_chatlog_settings(
    db: sqlite3.Connection, combined: dict[str, str], marker: str
) -> None:
    """Apply overrides and their marker within the caller's transaction."""
    db.execute("DELETE FROM chat_log_modes")
    db.execute("DELETE FROM notes WHERE scope LIKE ?", (CHATLOG_CAPTURE_NOTE_PREFIX + "%",))
    for key, mode in combined.items():
        db.execute(
            "INSERT INTO chat_log_modes(scope,mode) VALUES (?,?)",
            (key, "off" if mode == "off" else "on"),
        )
        db.execute(
            "INSERT INTO notes(scope,text) VALUES (?,?)",
            (CHATLOG_CAPTURE_NOTE_PREFIX + key, "all" if mode == "off" else mode),
        )
    db.execute("INSERT OR REPLACE INTO notes(scope,text) VALUES (?,?)", (marker, "1"))


def migrate_legacy_rag_override(db: sqlite3.Connection, encoded_modes: dict[str, str]) -> None:
    """Keep pre-existing DB rollout mode when upgrading to RAG_MODE."""
    row = db.execute("SELECT value FROM runtime_config WHERE key='retrieval_v2_mode'").fetchone()
    if row is None:
        return
    current = db.execute("SELECT 1 FROM runtime_config WHERE key='rag_mode'").fetchone()
    if current is None:
        try:
            legacy = json.loads(row["value"])
        except (TypeError, ValueError):
            legacy = None
        if isinstance(legacy, str) and legacy in encoded_modes:
            db.execute(
                "INSERT INTO runtime_config(key,value) VALUES (?,?)",
                ("rag_mode", encoded_modes[legacy]),
            )
    db.execute("DELETE FROM runtime_config WHERE key='retrieval_v2_mode'")
