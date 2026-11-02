"""Schema of the conversation-reset epoch audit table.

The table is created lazily by ``hina-reset-conversation``. ``ensure_epoch_schema``
runs inside that command's backed-up write transaction and still canonicalizes the
original exploratory columns; the standalone ``hina-migrate-reset-epochs`` command was
removed (#463). It never touches conversation rows, memories or other tables.
"""

from __future__ import annotations

import sqlite3

TABLE = "conversation_reset_epochs"
CURRENT_COLUMNS = frozenset({"id", "reset_at", "cutoff_message_id", "backup_sha256"})
LEGACY_COLUMNS = ("discarded_structured_turns", "discarded_shared_turns")
KNOWN_COLUMNS = CURRENT_COLUMNS | frozenset(LEGACY_COLUMNS)


def epoch_columns(db: sqlite3.Connection) -> frozenset[str]:
    return frozenset(row[1] for row in db.execute(f"PRAGMA table_info({TABLE})"))


def _validate_epoch_columns(columns: frozenset[str]) -> None:
    if columns and not {"id", "reset_at", "cutoff_message_id"} <= columns:
        raise RuntimeError("Unsupported conversation_reset_epochs schema: missing core columns")
    unexpected = columns - KNOWN_COLUMNS
    if unexpected:
        raise RuntimeError(
            "Unsupported conversation_reset_epochs columns: " + ", ".join(sorted(unexpected))
        )


def ensure_epoch_schema(db: sqlite3.Connection) -> bool:
    """Canonicalize the epoch table within the caller's open write transaction.

    SQLite ALTER TABLE operations are transactional. A failed reset rolls back
    both these DDL changes and its DELETE statements. Caller handles backup.
    """
    before = epoch_columns(db)
    _validate_epoch_columns(before)
    if not before:
        db.execute(
            """CREATE TABLE conversation_reset_epochs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reset_at TEXT NOT NULL,
                cutoff_message_id INTEGER NOT NULL,
                backup_sha256 TEXT NOT NULL
            )"""
        )
        return True

    if before == CURRENT_COLUMNS:
        return False

    if "backup_sha256" not in before:
        # Old epoch rows have no recoverable backup hash; '' is explicit unknown.
        db.execute(
            "ALTER TABLE conversation_reset_epochs "
            "ADD COLUMN backup_sha256 TEXT NOT NULL DEFAULT ''"
        )
    for name in LEGACY_COLUMNS:
        if name in before:
            db.execute(f"ALTER TABLE conversation_reset_epochs DROP COLUMN {name}")

    if epoch_columns(db) != CURRENT_COLUMNS:
        raise RuntimeError("Failed to canonicalize conversation_reset_epochs schema")
    return True
