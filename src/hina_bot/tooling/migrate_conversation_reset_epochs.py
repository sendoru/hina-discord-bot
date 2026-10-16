"""Offline, backed-up migration of obsolete conversation-reset epoch columns.

This is deliberately scoped to the epoch *audit* table. It does not touch
conversation rows, long-term memories, or any unrelated database tables.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from .maintenance_backup import BackupInfo, backup_sqlite
from .maintenance_db import connect_existing as _connect_existing

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


def migrate_epoch_schema(
    path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    offline_confirmed: bool = False,
) -> tuple[bool, BackupInfo | None]:
    """Create a verified snapshot, then migrate only the epoch schema."""
    if not offline_confirmed:
        raise ValueError("Stop all SQLite writers and pass --offline-confirmed")
    db = _connect_existing(path)
    try:
        before = epoch_columns(db)
        _validate_epoch_columns(before)
    finally:
        db.close()

    if before == CURRENT_COLUMNS:
        return False, None

    snapshot = backup_sqlite(path, backup_dir=backup_dir)
    db = _connect_existing(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            if epoch_columns(db) != before:
                raise RuntimeError("Epoch schema changed since backup; refuse migration")
            changed = ensure_epoch_schema(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return changed, snapshot
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safely drop obsolete epoch columns; preserve reset history and memories."
    )
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/hina.sqlite3"))
    parser.add_argument("--backup-dir", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--yes", action="store_true")
    parser.add_argument("--offline-confirmed", action="store_true")
    args = parser.parse_args(argv)
    try:
        db = _connect_existing(args.database)
        try:
            before = epoch_columns(db)
            _validate_epoch_columns(before)
        finally:
            db.close()
        print(f"Database: {args.database}")
        print(f"Existing epoch columns: {', '.join(sorted(before)) or '(none)'}")
        print(f"Legacy columns to drop: {', '.join(c for c in LEGACY_COLUMNS if c in before) or '(none)'}")
        print("Target epoch columns: id, reset_at, cutoff_message_id, backup_sha256")
        if not args.yes:
            print("Dry-run only; no schema changes.")
            return 0
        changed, snapshot = migrate_epoch_schema(
            args.database, backup_dir=args.backup_dir,
            offline_confirmed=args.offline_confirmed,
        )
        if changed:
            assert snapshot is not None
            print(f"Schema migrated; backup={snapshot.path}; sha256={snapshot.sha256}")
        else:
            print("Schema already current; nothing changed.")
        return 0
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
