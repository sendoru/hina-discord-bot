"""Offline conversation reset preserving structured long-term memories.

Stop the Discord bot and *every* SQLite writer before execution.
No memory extraction or shared-summary generation happens in this command.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .maintenance_backup import BackupInfo, backup_sqlite
from .reset_analysis_data import _connect_existing, _row_count, _table_exists

RAW_TABLES = ("turns", "shared_calls", "failed_turns", "shared_summaries")
PRESERVED_TABLES = (
    "memory_items", "memory_item_edit_history",
    "memory_reconciliation_proposals", "memory_extraction_cursors",
    "memory_extraction_batches", "memory_modes", "chat_log_modes", "notes",
    "runtime_config", "instructions", "runtime_knowledge", "emoji_registry",
    "guild_metadata", "channel_metadata",
)
DISCORD_EPOCH_MS = 1420070400000


@dataclass(frozen=True)
class ResetPlan:
    path: Path
    removed: dict[str, int]
    preserved: dict[str, int]
    pending_turns: int
    pending_scopes: int

    @property
    def ready(self) -> bool:
        return self.pending_turns == 0


@dataclass(frozen=True)
class ResetResult:
    backup: BackupInfo
    reset_id: int
    reset_at: str
    deleted: dict[str, int]


def _plan_from_connection(path: Path, db: sqlite3.Connection) -> ResetPlan:
    required = (*RAW_TABLES, "memory_items", "memory_extraction_cursors")
    missing = [name for name in required if not _table_exists(db, name)]
    if missing:
        raise ValueError("Missing Hina database tables: " + ", ".join(missing))
    row = db.execute(
        """SELECT COUNT(DISTINCT t.scope),COUNT(*)
           FROM turns t
           LEFT JOIN memory_extraction_cursors c ON c.scope=t.scope
           WHERE t.id>COALESCE(c.through_id,0)"""
    ).fetchone()
    return ResetPlan(
        path,
        {name: _row_count(db, name) for name in RAW_TABLES},
        {name: _row_count(db, name) for name in PRESERVED_TABLES if _table_exists(db,name)},
        int(row[1]),
        int(row[0]),
    )


def inspect_reset(path: str | Path) -> ResetPlan:
    db = _connect_existing(path)
    try:
        return _plan_from_connection(Path(path), db)
    finally:
        db.close()


def _remember_names(db: sqlite3.Connection) -> None:
    """Keep the best scoped speaker label after its source conversation is deleted."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS conversation_identity_names (
            scope TEXT PRIMARY KEY, name TEXT NOT NULL
        )"""
    )
    # Match Store.user_name lookup precedence: turns > shared_calls > summary.
    db.execute(
        """INSERT OR REPLACE INTO conversation_identity_names(scope,name)
           SELECT scope,name FROM shared_summaries WHERE name<>''"""
    )
    for table in ("shared_calls", "turns"):
        db.execute(
            f"""INSERT OR REPLACE INTO conversation_identity_names(scope,name)
                SELECT s.scope,s.name FROM {table} s
                WHERE s.name<>'' AND s.id=(
                    SELECT MAX(t.id) FROM {table} t
                    WHERE t.scope=s.scope AND t.name<>''
                )"""
        )


def reset_conversation(
    path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    offline_confirmed: bool = False,
) -> tuple[ResetPlan, ResetResult]:
    """Verified full backup then one atomic deletion of only conversation tables."""
    if not offline_confirmed:
        raise ValueError("Stop all SQLite writers and pass --offline-confirmed")
    path = Path(path)
    plan = inspect_reset(path)
    if not plan.ready:
        raise RuntimeError(
            f"Unextracted structured memory: {plan.pending_turns} turns "
            f"across {plan.pending_scopes} scopes; run hina-flush-memory first"
        )

    # This post-flush snapshot B is the immediate rollback point.
    snapshot = backup_sqlite(path, backup_dir=backup_dir)
    db = _connect_existing(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            locked = _plan_from_connection(path, db)
            if locked != plan or not locked.ready:
                raise RuntimeError("Database changed since backup; refuse reset")
            _remember_names(db)
            now = datetime.now(UTC)
            cutoff_message_id = (int(now.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22
            db.execute(
                """CREATE TABLE IF NOT EXISTS conversation_reset_epochs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reset_at TEXT NOT NULL,
                    cutoff_message_id INTEGER NOT NULL,
                    backup_sha256 TEXT NOT NULL
                )"""
            )
            epoch_info = {
                row[1]: row
                for row in db.execute("PRAGMA table_info(conversation_reset_epochs)")
            }
            if not {"id", "reset_at", "cutoff_message_id"} <= epoch_info.keys():
                raise RuntimeError("Unsupported conversation_reset_epochs schema")
            if "backup_sha256" not in epoch_info:
                # Older epoch tables predate backup tracking. Their original backup
                # hashes are unknown; use an empty sentinel only for those rows.
                # This migration is rolled back with the deletion on failure.
                db.execute(
                    "ALTER TABLE conversation_reset_epochs "
                    "ADD COLUMN backup_sha256 TEXT NOT NULL DEFAULT ''"
                )
                epoch_info = {
                    row[1]: row
                    for row in db.execute("PRAGMA table_info(conversation_reset_epochs)")
                }

            # Some older reset tables require a discarded-structured-turns count
            # without a DEFAULT. No such turns may be discarded here: the
            # structured-memory pending check above must be zero before reset.
            columns = ["reset_at", "cutoff_message_id", "backup_sha256"]
            values = [now.isoformat(), cutoff_message_id, snapshot.sha256]
            if "discarded_structured_turns" in epoch_info:
                columns.append("discarded_structured_turns")
                values.append(0)

            # Never invent values for other non-nullable legacy metadata. Check
            # before deleting turns, and roll back the migration on mismatch.
            missing_required = [
                name for name, (_, _, _, not_null, default, pk) in epoch_info.items()
                if not_null and default is None and not pk and name not in columns
            ]
            if missing_required:
                raise RuntimeError(
                    "Unsupported required conversation_reset_epochs columns: "
                    + ", ".join(missing_required)
                )

            deleted = {}
            for table in RAW_TABLES:
                result = db.execute(f"DELETE FROM {table}")
                deleted[table] = result.rowcount
            placeholders = ",".join("?" for _ in columns)
            result = db.execute(
                "INSERT INTO conversation_reset_epochs ("
                + ",".join(columns) + ") VALUES (" + placeholders + ")",
                values,
            )
            reset_id = int(result.lastrowid)
            # Be explicit about preserving other tables and extraction sequences.
            for table, count in plan.preserved.items():
                if _row_count(db, table) != count:
                    raise RuntimeError(f"Preserved data count changed: {table}")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return plan, ResetResult(snapshot, reset_id, now.isoformat(), deleted)
    finally:
        db.close()


def format_plan(plan: ResetPlan) -> str:
    lines = [
        f"Database: {plan.path}",
        "Conversation tables to delete:",
        *(f"  {table}: {count}" for table, count in plan.removed.items()),
        (f"Structured memory pending: {plan.pending_turns} turns / "
         f"{plan.pending_scopes} scopes"),
        f"Structured memory rows preserved: {plan.preserved.get('memory_items',0)}",
        "Shared summaries will be deleted (possibly unique information).",
        "Original conversation and shared summaries remain in the verified full backup.",
        "Telemetry, long-term memories, notes and settings will be preserved.",
        "Status: ready" if plan.ready else "Status: BLOCKED; flush memory first",
    ]
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline conversation reset (stopped bot, verified full backup required)"
    )
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH","data/hina.sqlite3"))
    parser.add_argument("--backup-dir", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--yes", action="store_true")
    parser.add_argument("--offline-confirmed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = inspect_reset(args.database)
        print(format_plan(plan))
        if not args.yes:
            print("Dry-run only; no conversation rows changed.")
            return 0
        if not args.offline_confirmed:
            print("ERROR: Stop all DB writers and pass --offline-confirmed")
            return 2
        _, result = reset_conversation(
            args.database,
            backup_dir=args.backup_dir,
            offline_confirmed=True,
        )
        print(
            f"Reset #{result.reset_id} completed at {result.reset_at}; "
            f"backup={result.backup.path}; sha256={result.backup.sha256}"
        )
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
