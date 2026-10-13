"""Offline structured-memory catch-up, independent of conversation deletion.

The bot and all dashboard/database writers MUST be stopped before --yes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.ai.providers import create_provider_client
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.scope_overrides import MemoryMode
from hina_bot.core.store import Store

from .maintenance_backup import BackupInfo, backup_sqlite


@dataclass(frozen=True)
class PendingScope:
    scope: Scope
    count: int


def _decode_scope(row) -> Scope:
    value = str(row["scope"])
    fields = value.split(":")
    if len(fields) != 6 or fields[2] != "channel" or fields[4] != "user":
        raise ValueError("Invalid structured memory scope")
    user_id = int(row["user_id"])
    if fields[5] != str(user_id):
        raise ValueError("Mismatched structured memory owner")
    if fields[0] == "guild":
        guild_id = int(fields[1])
    elif fields[0] == "dm" and fields[1] == str(user_id):
        guild_id = None
    else:
        raise ValueError("Invalid structured memory realm")
    scope = Scope(
        guild_id, int(fields[3]), user_id,
        bool(int(row["all_public"])),
    )
    if scope.conversation != value or scope.realm != row["realm"]:
        raise ValueError("Inconsistent structured memory scope")
    return scope


def pending_scopes(db: sqlite3.Connection) -> tuple[PendingScope, ...]:
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT t.scope,t.realm,t.user_id,COUNT(*) AS pending,
                  MIN(t.exportable) AS all_public,MIN(t.id) AS oldest
           FROM turns t
           LEFT JOIN memory_extraction_cursors c ON c.scope=t.scope
           WHERE t.id>COALESCE(c.through_id,0)
           GROUP BY t.scope,t.realm,t.user_id
           ORDER BY oldest,t.scope"""
    ).fetchall()
    return tuple(PendingScope(_decode_scope(row), int(row["pending"])) for row in rows)


def inspect_pending(path: str | Path) -> tuple[PendingScope, ...]:
    candidate = Path(path)
    if not candidate.is_file():
        raise FileNotFoundError(f"Database not found: {candidate}")
    db = sqlite3.connect(f"file:{candidate.resolve()}?mode=ro", uri=True)
    try:
        return pending_scopes(db)
    finally:
        db.close()


class _ExtractionWorker(MemorySummaryMixin):
    def __init__(self, settings):
        self.settings = settings
        self.client = create_provider_client(settings, settings.provider)
        self.usage = UsageLogger(settings.usage_log_path)

    async def close(self):
        try:
            await self.client.close()
        finally:
            self.usage.close()


async def flush_pending(store: Store, worker) -> tuple[int, tuple[str, ...]]:
    """Return committed batch count and content-free errors; never advance failed cursors."""
    failures: list[str] = []
    batches = 0
    for entry in pending_scopes(store.db):
        scope = entry.scope
        if not MemoryMode(store.memory_mode(scope)).writes:
            failures.append(f"{scope.conversation}: memory writes are disabled")
            continue
        while store.pending_memory_extraction(scope):
            previous = store.memory_extraction_cursor(scope)
            try:
                committed = await worker.extract_structured_memory(
                    store, scope, min_turns=1
                )
                if not committed or store.memory_extraction_cursor(scope) <= previous:
                    failures.append(f"{scope.conversation}: no committed progress")
                    break
                batches += 1
            except Exception as exc:  # noqa: BLE001 - independent scopes may continue
                failures.append(f"{scope.conversation}: {type(exc).__name__}")
                break
    return batches, tuple(failures)


@dataclass(frozen=True)
class FlushResult:
    backup: BackupInfo
    batches: int
    failures: tuple[str, ...]
    remaining: tuple[PendingScope, ...]

    @property
    def completed(self) -> bool:
        return not self.failures and not self.remaining


async def run_flush(
    path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    worker_factory=None,
) -> FlushResult:
    """Execute only on a stopped bot. A pre-extraction full SQLite backup is mandatory."""
    database = Path(path)
    backup = backup_sqlite(database, backup_dir=backup_dir)
    base = Settings.load()
    if Path(base.db_path).resolve() != database.resolve():
        base = replace(base, db_path=str(database))
    store = Store(str(database), history_turns=base.history_turns)
    worker = None
    try:
        settings = RuntimeSettings(base, store)
        worker = (
            worker_factory(settings)
            if worker_factory is not None
            else _ExtractionWorker(settings)
        )
        batches, failures = await flush_pending(store, worker)
        return FlushResult(backup, batches, failures, pending_scopes(store.db))
    finally:
        try:
            if worker is not None:
                await worker.close()
        finally:
            store.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Back up and flush pending structured memories, with the bot stopped"
    )
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "data/hina.sqlite3"))
    parser.add_argument("--backup-dir", default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--yes", action="store_true")
    parser.add_argument(
        "--offline-confirmed", action="store_true",
        help="I have stopped the bot and all SQLite writers",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        entries = inspect_pending(args.database)
        print(
            f"Pending structured memory: {sum(x.count for x in entries)} turns "
            f"across {len(entries)} scopes"
        )
        if not args.yes:
            print("Dry-run; no LLM requests or database modifications.")
            return 0
        if not args.offline_confirmed:
            print("ERROR: Stop all writers and pass --offline-confirmed")
            return 2
        result = asyncio.run(
            run_flush(args.database, backup_dir=args.backup_dir)
        )
        print(f"Pre-flush backup: {result.backup.path} SHA256={result.backup.sha256}")
        print(f"Committed batches: {result.batches}")
        print(
            f"Remaining: {sum(x.count for x in result.remaining)} turns "
            f"across {len(result.remaining)} scopes"
        )
        for error in result.failures:
            print("BLOCKED: " + error)
        return 0 if result.completed else 2
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
