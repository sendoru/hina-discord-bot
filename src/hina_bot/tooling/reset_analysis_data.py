"""Offline reset for raw conversation/observability analysis data.

This command intentionally preserves persistent memory and runtime/admin configuration. It must be
run while the bot is stopped because active RotatingFileHandler instances may keep writing to
unlinked telemetry files.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


RAW_TABLES = ("turns", "shared_calls")
PRESERVED_TABLES = (
    "memory_items",
    "summaries",
    "shared_summaries",
    "memory_reconciliation_proposals",
    "memory_extraction_cursors",
    "memory_modes",
    "chat_log_modes",
    "notes",
    "emoji_registry",
    "runtime_config",
    "instructions",
    "runtime_knowledge",
    "admin_migrations",
    "observability_epochs",
)


class UnsafeAnalysisReset(RuntimeError):
    """Raised when raw rows still contain uncommitted persistent-memory work."""


@dataclass(frozen=True)
class PendingWork:
    kind: str
    scopes: int
    turns: int


@dataclass(frozen=True)
class ResetPlan:
    database_path: Path
    raw_rows: dict[str, int]
    preserved_rows: dict[str, int]
    pending: tuple[PendingWork, ...]
    telemetry_files: tuple[Path, ...]
    telemetry_bytes: int

    @property
    def safe(self) -> bool:
        return not any(item.turns for item in self.pending)


@dataclass(frozen=True)
class ResetResult:
    epoch_id: int
    reset_at: str
    deleted_files: tuple[Path, ...]


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _row_count(db: sqlite3.Connection, table: str) -> int:
    if not _table_exists(db, table):
        return 0
    return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _pending_work(db: sqlite3.Connection) -> tuple[PendingWork, ...]:
    pending: list[PendingWork] = []

    if _table_exists(db, "turns"):
        if _table_exists(db, "summaries"):
            summary_cursor = "COALESCE(s.through_id,0)"
            summary_join = "LEFT JOIN summaries s ON s.scope=t.scope"
        else:
            summary_cursor = "0"
            summary_join = ""
        row = db.execute(
            f"""SELECT COUNT(DISTINCT t.scope),COUNT(*)
                FROM turns t
                {summary_join}
                WHERE t.id>{summary_cursor}"""
        ).fetchone()
        pending.append(PendingWork("personal_summary", int(row[0]), int(row[1])))

        if _table_exists(db, "memory_extraction_cursors"):
            extraction_cursor = "COALESCE(c.through_id,s.through_id,0)"
            extraction_join = (
                "LEFT JOIN memory_extraction_cursors c ON c.scope=t.scope "
                "LEFT JOIN summaries s ON s.scope=t.scope"
                if _table_exists(db, "summaries")
                else "LEFT JOIN memory_extraction_cursors c ON c.scope=t.scope"
            )
            if not _table_exists(db, "summaries"):
                extraction_cursor = "COALESCE(c.through_id,0)"
        else:
            extraction_cursor = summary_cursor
            extraction_join = summary_join
        row = db.execute(
            f"""SELECT COUNT(DISTINCT t.scope),COUNT(*)
                FROM turns t
                {extraction_join}
                WHERE t.id>{extraction_cursor}"""
        ).fetchone()
        pending.append(PendingWork("structured_memory", int(row[0]), int(row[1])))

    if _table_exists(db, "shared_calls"):
        if _table_exists(db, "shared_summaries"):
            shared_cursor = "COALESCE(s.through_id,0)"
            shared_join = "LEFT JOIN shared_summaries s ON s.scope=t.scope"
        else:
            shared_cursor = "0"
            shared_join = ""
        row = db.execute(
            f"""SELECT COUNT(DISTINCT t.scope),COUNT(*)
                FROM shared_calls t
                {shared_join}
                WHERE t.id>{shared_cursor}"""
        ).fetchone()
        pending.append(PendingWork("shared_summary", int(row[0]), int(row[1])))

    return tuple(pending)


def _rotated_files(path: Path) -> set[Path]:
    files: set[Path] = set()
    if path.is_file():
        files.add(path)
    if not path.parent.exists():
        return files
    prefix = path.name + "."
    for candidate in path.parent.glob(path.name + ".*"):
        suffix = candidate.name[len(prefix) :]
        if suffix.isdigit() and candidate.is_file():
            files.add(candidate)
    return files


def telemetry_files(usage_log_path: str, event_log_path: str) -> tuple[Path, ...]:
    bases: set[Path] = set()
    if usage_log_path.strip():
        usage = Path(usage_log_path)
        bases.add(usage)
        bases.add(usage.with_name("discord-usage.jsonl"))
    if event_log_path.strip():
        bases.add(Path(event_log_path))

    files: set[Path] = set()
    for base in bases:
        files.update(_rotated_files(base))
    return tuple(sorted(files, key=lambda path: str(path)))


def _connect_existing(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")
    db = sqlite3.connect(str(path), timeout=1.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=1000")
    if not _table_exists(db, "turns"):
        db.close()
        raise ValueError(f"Not a hina runtime database (missing turns table): {path}")
    return db


def build_reset_plan(
    database_path: str | Path,
    *,
    usage_log_path: str = "data/logs/usage.jsonl",
    event_log_path: str = "data/logs/events.jsonl",
) -> ResetPlan:
    path = Path(database_path)
    db = _connect_existing(path)
    try:
        raw_rows = {table: _row_count(db, table) for table in RAW_TABLES}
        preserved_rows = {
            table: _row_count(db, table)
            for table in PRESERVED_TABLES
            if _table_exists(db, table)
        }
        pending = _pending_work(db)
    finally:
        db.close()

    files = telemetry_files(usage_log_path, event_log_path)
    size = sum(item.stat().st_size for item in files if item.is_file())
    return ResetPlan(path, raw_rows, preserved_rows, pending, files, size)


def _assert_safe(plan: ResetPlan) -> None:
    blockers = [item for item in plan.pending if item.turns]
    if not blockers:
        return
    detail = ", ".join(
        f"{item.kind}={item.turns} turns/{item.scopes} scopes" for item in blockers
    )
    raise UnsafeAnalysisReset(
        "Refusing to discard raw rows with pending persistent-memory work: " + detail
    )


def reset_analysis_data(
    database_path: str | Path,
    *,
    usage_log_path: str = "data/logs/usage.jsonl",
    event_log_path: str = "data/logs/events.jsonl",
    apply: bool = False,
) -> tuple[ResetPlan, ResetResult | None]:
    """Plan or perform one offline analysis baseline reset."""

    plan = build_reset_plan(
        database_path,
        usage_log_path=usage_log_path,
        event_log_path=event_log_path,
    )
    if not apply:
        return plan, None
    _assert_safe(plan)

    db = _connect_existing(database_path)
    reset_at = datetime.now(UTC).isoformat()
    try:
        db.execute("BEGIN IMMEDIATE")
        # Re-check after acquiring the write lock so a racing writer cannot slip new pending rows
        # between the dry-run plan and destructive DELETEs.
        locked_plan = ResetPlan(
            plan.database_path,
            {table: _row_count(db, table) for table in RAW_TABLES},
            {
                table: _row_count(db, table)
                for table in PRESERVED_TABLES
                if _table_exists(db, table)
            },
            _pending_work(db),
            plan.telemetry_files,
            plan.telemetry_bytes,
        )
        _assert_safe(locked_plan)

        db.execute(
            """CREATE TABLE IF NOT EXISTS observability_epochs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   reset_at TEXT NOT NULL
               )"""
        )
        for table in RAW_TABLES:
            if _table_exists(db, table):
                db.execute(f"DELETE FROM {table}")
        cursor = db.execute(
            "INSERT INTO observability_epochs(reset_at) VALUES (?)",
            (reset_at,),
        )
        epoch_id = int(cursor.lastrowid)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    deleted_files: list[Path] = []
    for path in plan.telemetry_files:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        deleted_files.append(path)

    return plan, ResetResult(epoch_id, reset_at, tuple(deleted_files))


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.1f} MiB"


def format_plan(plan: ResetPlan) -> str:
    lines = [
        f"Database: {plan.database_path}",
        "",
        "Raw rows to delete:",
    ]
    lines.extend(f"  {name}: {count}" for name, count in plan.raw_rows.items())
    lines.extend(["", "Persistent rows preserved:"])
    if plan.preserved_rows:
        lines.extend(f"  {name}: {count}" for name, count in plan.preserved_rows.items())
    else:
        lines.append("  (none found)")

    lines.extend(["", "Pending persistent-memory work:"])
    if plan.pending:
        for item in plan.pending:
            lines.append(f"  {item.kind}: {item.turns} turns across {item.scopes} scopes")
    else:
        lines.append("  (none)")

    lines.extend(
        [
            "",
            f"Telemetry files to delete: {len(plan.telemetry_files)} "
            f"({_format_bytes(plan.telemetry_bytes)})",
        ]
    )
    lines.extend(f"  {path}" for path in plan.telemetry_files)
    if not plan.telemetry_files:
        lines.append("  (none found)")

    lines.extend(
        [
            "",
            (
                "Safety: ready for reset"
                if plan.safe
                else "Safety: BLOCKED until pending persistent-memory work is committed"
            ),
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reset raw conversation/telemetry analysis data while preserving persistent memory "
            "and runtime configuration. Stop the bot before running this command."
        )
    )
    parser.add_argument(
        "--database",
        default=os.getenv("DATABASE_PATH", "data/hina.sqlite3"),
        help="runtime SQLite path (default: DATABASE_PATH or data/hina.sqlite3)",
    )
    parser.add_argument(
        "--usage-log",
        default=os.getenv("USAGE_LOG_PATH", "data/logs/usage.jsonl"),
        help="usage JSONL path; discord-usage.jsonl is derived from its directory",
    )
    parser.add_argument(
        "--event-log",
        default=os.getenv("EVENT_LOG_PATH", "data/logs/events.jsonl"),
        help="event JSONL path",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="show the reset plan without modifying anything (default)",
    )
    mode.add_argument(
        "--yes",
        action="store_true",
        help="perform the destructive reset after safety checks",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan, result = reset_analysis_data(
            args.database,
            usage_log_path=args.usage_log,
            event_log_path=args.event_log,
            apply=args.yes,
        )
    except UnsafeAnalysisReset as exc:
        # Rebuild the plan for useful operator output; the command is offline so it should be stable.
        plan = build_reset_plan(
            args.database,
            usage_log_path=args.usage_log,
            event_log_path=args.event_log,
        )
        print(format_plan(plan))
        print(f"\nERROR: {exc}")
        return 2
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2

    print(format_plan(plan))
    if result is None:
        print("\nDry run only. Stop the bot and rerun with --yes to apply this reset.")
    else:
        print(
            f"\nReset complete: observability epoch #{result.epoch_id} "
            f"at {result.reset_at}; deleted {len(result.deleted_files)} telemetry files."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
