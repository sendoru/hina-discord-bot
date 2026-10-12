"""Offline catch-up for structured memory and public shared-summary tails.

This uses the runtime's extraction/summarization path, not a cursor fast-forward.
Stop the Discord bot and every SQLite writer before running it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import MemoryMode

from .reset_conversation_state import (
    _backup_destination,
    _verified_backup,
    build_conversation_reset_plan,
)
from .reset_analysis_data import _connect_existing


@dataclass(frozen=True)
class PendingScope:
    scope: Scope
    turns: int


@dataclass(frozen=True)
class MemoryFlushPlan:
    structured: tuple[PendingScope, ...]
    shared: tuple[PendingScope, ...]

    @property
    def total_turns(self) -> int:
        return sum(row.turns for row in (*self.structured, *self.shared))


@dataclass(frozen=True)
class MemoryFlushResult:
    backup_path: Path
    backup_sha256: str
    structured_batches: int
    shared_batches: int
    errors: tuple[str, ...]
    remaining: MemoryFlushPlan

    @property
    def success(self) -> bool:
        return not self.errors and not self.remaining.total_turns


def _decode_scope(value: str, realm: str, user_id: str) -> Scope:
    parts = str(value).split(":")
    if len(parts) != 6 or parts[2] != "channel" or parts[4] != "user":
        raise ValueError("Invalid pending memory scope format")
    if parts[5] != str(user_id):
        raise ValueError("Pending memory scope does not match its owner")
    if parts[0] == "guild":
        guild_id: int | None = int(parts[1])
    elif parts[0] == "dm":
        guild_id = None
        if parts[1] != str(user_id):
            raise ValueError("DM memory scope owner mismatch")
    else:
        raise ValueError("Unknown pending memory scope realm")
    channel_id = int(parts[3])
    owner_id = int(user_id)
    if (guild_id is not None and guild_id <= 0) or channel_id <= 0 or owner_id <= 0:
        raise ValueError("Invalid pending memory scope ID")
    scope = Scope(guild_id, channel_id, owner_id)
    if scope.conversation != value or scope.realm != realm:
        raise ValueError("Pending memory scope/realm mismatch")
    return scope


def _pending_rows(db: sqlite3.Connection, *, shared: bool) -> tuple[PendingScope, ...]:
    if shared:
        query = """SELECT t.scope,t.realm,t.user_id,COUNT(*) AS pending
                   FROM shared_calls t
                   LEFT JOIN shared_summaries s ON s.scope=t.scope
                   WHERE t.id>COALESCE(s.through_id,0)
                   GROUP BY t.scope,t.realm,t.user_id
                   ORDER BY MIN(t.id),t.scope"""
    else:
        query = """SELECT t.scope,t.realm,t.user_id,COUNT(*) AS pending
                   FROM turns t
                   LEFT JOIN memory_extraction_cursors c ON c.scope=t.scope
                   WHERE t.id>COALESCE(c.through_id,0)
                   GROUP BY t.scope,t.realm,t.user_id
                   ORDER BY MIN(t.id),t.scope"""
    return tuple(
        PendingScope(
            _decode_scope(str(row["scope"]), str(row["realm"]), str(row["user_id"])),
            int(row["pending"]),
        )
        for row in db.execute(query)
    )


def pending_memory_plan(db: sqlite3.Connection) -> MemoryFlushPlan:
    """List every pending scope, including one-turn tails and disabled modes."""
    return MemoryFlushPlan(
        structured=_pending_rows(db, shared=False),
        shared=_pending_rows(db, shared=True),
    )


def build_memory_flush_plan(path: str | Path) -> MemoryFlushPlan:
    db = _connect_existing(path)
    try:
        return pending_memory_plan(db)
    finally:
        db.close()


async def flush_memory_batches(store, worker) -> tuple[int, int, tuple[str, ...]]:
    """Process each scope until all work commits or a failure stops that scope.

    The runtime's source-ID/cursor logic remains authoritative; don't synthesize
    memories, force-advance cursors, or bypass read-only/off memory modes.
    """
    plan = pending_memory_plan(store.db)
    errors: list[str] = []
    counts = [0, 0]
    for index, (kind, scopes) in enumerate(
        (("structured", plan.structured), ("shared", plan.shared))
    ):
        for pending_scope in scopes:
            scope = pending_scope.scope
            if not MemoryMode(store.memory_mode(scope)).writes:
                errors.append(f"{kind} {scope.conversation}: memory writes disabled")
                continue
            while True:
                pending = (
                    store.pending_memory_extraction(scope)
                    if kind == "structured"
                    else store.pending_shared(scope)
                )
                if not pending:
                    break
                previous = int(
                    store.memory_extraction_cursor(scope)
                    if kind == "structured"
                    else store.shared_summary(scope)[1]
                )
                try:
                    if kind == "structured":
                        success = await worker.extract_structured_memory(
                            store, scope, min_turns=1
                        )
                        advanced = store.memory_extraction_cursor(scope)
                    else:
                        success = await worker.summarize_shared(
                            store, scope, min_turns=1
                        )
                        advanced = store.shared_summary(scope)[1]
                except Exception as exc:  # noqa: BLE001 - independent scopes; no content in errors
                    errors.append(
                        f"{kind} {scope.conversation}: {type(exc).__name__}"
                    )
                    break
                if not success or int(advanced) <= previous:
                    errors.append(f"{kind} {scope.conversation}: no committed progress")
                    break
                counts[index] += 1
    return counts[0], counts[1], tuple(errors)


async def flush_memory_database(
    path: str | Path,
    *,
    offline_confirmed: bool,
    backup_dir: str | Path | None = None,
    worker_factory=None,
) -> MemoryFlushResult:
    """Backup first, then flush all scopes; never discard pending work on failure."""
    if not offline_confirmed:
        raise ValueError("Stop all bot and dashboard DB writers and confirm offline mode")
    path = Path(path)
    initial = build_conversation_reset_plan(path)
    backup = _backup_destination(path, backup_dir)
    source = _connect_existing(path)
    try:
        backup_sha = _verified_backup(source, initial, backup)
    finally:
        source.close()

    # Use the same provider and runtime overrides that production uses.
    from hina_bot.core.config import Settings
    from hina_bot.core.runtime_config import RuntimeSettings
    from hina_bot.core.store import Store

    base = Settings.load()
    if Path(base.db_path).resolve() != path.resolve():
        from dataclasses import replace

        base = replace(base, db_path=str(path))
    store = Store(str(path), history_turns=base.history_turns)
    worker = None
    try:
        settings = RuntimeSettings(base, store)
        if worker_factory is not None:
            worker = worker_factory(settings)
        else:
            from hina_bot.ai.providers import create_provider_client
            from hina_bot.ai.memory_summary import MemorySummaryMixin
            from hina_bot.ai.usage import UsageLogger

            class OfflineMemoryWorker(MemorySummaryMixin):
                def __init__(self, runtime_settings):
                    self.settings = runtime_settings
                    self.client = create_provider_client(
                        runtime_settings, runtime_settings.provider
                    )
                    self.usage = UsageLogger(runtime_settings.usage_log_path)

                async def close(self):
                    try:
                        await self.client.close()
                    finally:
                        self.usage.close()

            worker = OfflineMemoryWorker(settings)

        structured, shared, errors = await flush_memory_batches(store, worker)
        remaining = pending_memory_plan(store.db)
        return MemoryFlushResult(
            backup, backup_sha, structured, shared, errors, remaining
        )
    finally:
        try:
            if worker is not None:
                close = getattr(worker, "close", None)
                if callable(close):
                    await close()
        finally:
            store.close()


def format_flush_plan(plan: MemoryFlushPlan) -> str:
    return (
        f"Structured memory: {sum(x.turns for x in plan.structured)} turns / "
        f"{len(plan.structured)} scopes\n"
        f"Shared summaries: {sum(x.turns for x in plan.shared)} turns / "
        f"{len(plan.shared)} scopes"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Flush all pending memory tails offline before resetting conversation data"
    )
    parser.add_argument(
        "--database", default=os.getenv("DATABASE_PATH", "data/hina.sqlite3")
    )
    parser.add_argument("--backup-dir", default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--yes", action="store_true")
    parser.add_argument("--offline-confirmed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = build_memory_flush_plan(args.database)
        print(format_flush_plan(plan))
        if not args.yes:
            print("Dry run only. No memory extraction or LLM requests were made.")
            return 0
        result = asyncio.run(
            flush_memory_database(
                args.database,
                offline_confirmed=args.offline_confirmed,
                backup_dir=args.backup_dir,
            )
        )
    except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        print(f"ERROR: {type(exc).__name__}")
        return 2

    print(
        f"Completed: structured={result.structured_batches} batches, "
        f"shared={result.shared_batches} batches; backup={result.backup_path}; "
        f"sha256={result.backup_sha256}"
    )
    print("Remaining:\n" + format_flush_plan(result.remaining))
    if result.errors:
        for error in result.errors:
            print("FAILED: " + error)
    return 0 if result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
