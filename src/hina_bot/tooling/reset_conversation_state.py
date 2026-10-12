"""Offline operational conversation reset with verified SQLite backup.

Unlike reset_analysis_data, this keeps telemetry for before/after comparisons and also
clears persisted shared summaries. It must only run with the bot and all DB writers stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .reset_analysis_data import (
    PendingWork,
    UnsafeAnalysisReset,
    _connect_existing,
    _pending_work,
    _row_count,
    _table_exists,
)

CONVERSATION_TABLES = ("turns", "shared_calls", "failed_turns", "shared_summaries")
PRESERVED_TABLES = (
    "memory_items",
    "memory_item_edit_history",
    "memory_reconciliation_proposals",
    "memory_extraction_cursors",
    "memory_modes",
    "chat_log_modes",
    "notes",
    "runtime_config",
    "instructions",
    "runtime_knowledge",
    "emoji_registry",
    "guild_metadata",
    "channel_metadata",
    "admin_commands",
    "admin_migrations",
    "observability_epochs",
)
REQUIRED_TABLES = (
    "turns",
    "shared_calls",
    "shared_summaries",
    "memory_items",
    "memory_extraction_cursors",
)
# This is a Discord snowflake epoch, NOT a Unix timestamp.
DISCORD_EPOCH_MS = 1420070400000


@dataclass(frozen=True)
class ConversationResetPlan:
    database_path: Path
    removed_rows: dict[str, int]
    preserved_rows: dict[str, int]
    pending: tuple[PendingWork, ...]

    @property
    def safe(self) -> bool:
        return not any(row.turns for row in self.pending)


@dataclass(frozen=True)
class ConversationResetResult:
    backup_path: Path
    backup_sha256: str
    cutoff_at: str
    cutoff_message_id: int
    reset_id: int
    deleted_rows: dict[str, int]


def _counts(db: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {table: _row_count(db, table) for table in tables if _table_exists(db, table)}


def _plan_from_connection(
    path: Path,
    db: sqlite3.Connection,
) -> ConversationResetPlan:
    missing = [table for table in REQUIRED_TABLES if not _table_exists(db, table)]
    if missing:
        raise ValueError(f"Not a supported Hina database (missing {', '.join(missing)})")
    return ConversationResetPlan(
        path,
        _counts(db, CONVERSATION_TABLES),
        _counts(db, PRESERVED_TABLES),
        _pending_work(db),
    )


def build_conversation_reset_plan(path: str | Path) -> ConversationResetPlan:
    db = _connect_existing(path)
    try:
        return _plan_from_connection(Path(path), db)
    finally:
        db.close()


def _backup_destination(database_path: Path, backup_dir: str | Path | None) -> Path:
    directory = (
        Path(backup_dir)
        if backup_dir is not None
        else database_path.parent / "backups"
    )
    if directory.exists() and directory.is_symlink():
        raise ValueError("Backup directory must not be a symbolic link")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Avoid silently changing permissions of an operator-supplied existing directory.
    if directory.stat().st_mode & 0o077:
        raise ValueError(
            "Backup directory must be owner-only (chmod 700), "
            "because snapshots contain private messages"
        )
    if directory.resolve() == database_path.parent.resolve() and backup_dir is None:
        raise ValueError("Backup must be stored outside the live database directory")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return directory / (
        f"{database_path.stem}-conversation-{timestamp}-{uuid.uuid4().hex[:12]}.sqlite3"
    )


def _verified_backup(
    source: sqlite3.Connection,
    plan: ConversationResetPlan,
    backup_path: Path,
) -> str:
    """Create a private, non-overwriting SQLite snapshot and validate it."""
    temp_path = backup_path.with_suffix(backup_path.suffix + ".partial")
    fd = os.open(str(temp_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        snapshot = sqlite3.connect(str(temp_path))
        try:
            source.backup(snapshot)
            snapshot.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity_check failed")
            actual = _plan_from_connection(plan.database_path, snapshot)
            if (
                actual.removed_rows != plan.removed_rows
                or actual.preserved_rows != plan.preserved_rows
                or actual.pending != plan.pending
            ):
                raise RuntimeError("Database changed during backup; stop all writers")
        finally:
            snapshot.close()

        temp_path.chmod(0o600)
        # The name includes a random component and the staging file is O_EXCL.
        # Refuse replacement if a path was somehow taken between creation and rename.
        if backup_path.exists():
            raise FileExistsError(f"Backup already exists: {backup_path}")
        os.rename(temp_path, backup_path)
        digest = hashlib.sha256()
        with backup_path.open("rb") as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(part)
        return digest.hexdigest()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def reset_conversation_state(
    path: str | Path,
    *,
    apply: bool = False,
    offline_confirmed: bool = False,
    discard_pending: bool = False,
    backup_dir: str | Path | None = None,
) -> tuple[ConversationResetPlan, ConversationResetResult | None]:
    """Preserve structured memories and configuration while discarding conversation state.

    This command does not stop bot processes. The caller MUST stop every database writer.
    """
    plan = build_conversation_reset_plan(path)
    if not apply:
        return plan, None
    if not offline_confirmed:
        raise ValueError("Stop bot and dashboard writers, then pass --offline-confirmed")
    if not plan.safe and not discard_pending:
        raise UnsafeAnalysisReset(
            "Pending memory extraction or shared summary; inspect --dry-run and "
            "pass --discard-pending only if intentionally abandoning those tails."
        )

    source = _connect_existing(path)
    before_version = int(source.execute("PRAGMA data_version").fetchone()[0])
    backup_path = _backup_destination(plan.database_path, backup_dir)
    try:
        backup_hash = _verified_backup(source, plan, backup_path)
        # Acquire SQLite's write lock and revalidate to reject races after snapshot.
        source.execute("BEGIN IMMEDIATE")
        try:
            version = int(source.execute("PRAGMA data_version").fetchone()[0])
            locked_plan = _plan_from_connection(plan.database_path, source)
            if version != before_version or locked_plan != plan:
                raise RuntimeError("Database changed between plan/backup and reset")
            if not locked_plan.safe and not discard_pending:
                raise UnsafeAnalysisReset("Memory work became pending during reset")

            now = datetime.now(UTC)
            cutoff_at = now.isoformat()
            cutoff_message_id = (int(now.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22
            source.execute(
                """CREATE TABLE IF NOT EXISTS conversation_reset_epochs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reset_at TEXT NOT NULL,
                    cutoff_message_id INTEGER NOT NULL,
                    discarded_structured_turns INTEGER NOT NULL,
                    discarded_shared_turns INTEGER NOT NULL
                )"""
            )
            deleted: dict[str, int] = {}
            for table in CONVERSATION_TABLES:
                cursor = source.execute(f"DELETE FROM {table}")
                deleted[table] = max(0, int(cursor.rowcount or 0))
            pending_counts = {row.kind: row.turns for row in locked_plan.pending}
            cursor = source.execute(
                """INSERT INTO conversation_reset_epochs (
                    reset_at,cutoff_message_id,discarded_structured_turns,
                    discarded_shared_turns
                ) VALUES (?,?,?,?)""",
                (
                    cutoff_at,
                    cutoff_message_id,
                    pending_counts.get("structured_memory", 0),
                    pending_counts.get("shared_summary", 0),
                ),
            )
            reset_id = int(cursor.lastrowid)
            source.commit()
        except BaseException:
            source.rollback()
            raise

        # Secure-delete is enabled in _connect_existing; fold empty WAL pages.
        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return plan, ConversationResetResult(
            backup_path, backup_hash, cutoff_at, cutoff_message_id, reset_id, deleted
        )
    finally:
        source.close()


def format_plan(plan: ConversationResetPlan) -> str:
    lines = [f"Database: {plan.database_path}", "", "Conversation rows to reset:"]
    lines.extend(f"  {name}: {count}" for name, count in plan.removed_rows.items())
    lines.extend(["", "Persistent rows preserved:"])
    lines.extend(f"  {name}: {count}" for name, count in plan.preserved_rows.items())
    lines.extend(["", "Pending memory work to lose without extraction:"])
    lines.extend(
        f"  {row.kind}: {row.turns} turns across {row.scopes} scopes"
        for row in plan.pending
    )
    lines.append(
        "\nSafety: ready" if plan.safe
        else "\nSafety: BLOCKED unless an explicit --discard-pending is supplied"
    )
    lines.append("Existing telemetry logs will be preserved; full SQLite backup is mandatory.")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline operational conversation reset with verified full SQLite backup."
    )
    parser.add_argument(
        "--database", default=os.getenv("DATABASE_PATH", "data/hina.sqlite3")
    )
    parser.add_argument("--backup-dir", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--yes", action="store_true", help="Perform reset (not dry run)")
    parser.add_argument(
        "--offline-confirmed", action="store_true",
        help="Confirm bot and dashboard DB writers have been stopped",
    )
    parser.add_argument(
        "--discard-pending", action="store_true",
        help="Explicitly abandon pending memory/summary work after a verified backup",
    )
    parser.add_argument(
        "--flush-pending", action="store_true",
        help="Back up and flush pending structured/shared memory with the LLM before reset",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.flush_pending and args.discard_pending:
        print("ERROR: --flush-pending and --discard-pending cannot be combined")
        return 2
    try:
        if args.yes and args.flush_pending:
            from .flush_memory import flush_memory_database

            flushed = asyncio.run(
                flush_memory_database(
                    args.database,
                    offline_confirmed=args.offline_confirmed,
                    backup_dir=args.backup_dir,
                )
            )
            print(
                f"Pre-reset flush backup: {flushed.backup_path}; "
                f"structured batches={flushed.structured_batches}, "
                f"shared batches={flushed.shared_batches}"
            )
            if not flushed.success:
                print(
                    "ERROR: Pending memory flush incomplete. "
                    "No conversation reset was performed."
                )
                for error in flushed.errors:
                    print("FAILED: " + error)
                return 2
        plan, result = reset_conversation_state(
            args.database,
            apply=args.yes,
            offline_confirmed=args.offline_confirmed,
            discard_pending=args.discard_pending,
            backup_dir=args.backup_dir,
        )
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 2

    print(format_plan(plan))
    if result is None:
        print("\nDry run only. No database contents were modified.")
        return 0
    print(
        f"\nReset #{result.reset_id} at {result.cutoff_at}; "
        f"backup={result.backup_path}; sha256={result.backup_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
