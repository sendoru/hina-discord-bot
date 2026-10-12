"""Unit tests for offline memory catch-up without external provider calls."""

import sqlite3
from types import SimpleNamespace as NS

import pytest

from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.flush_memory import (
    build_memory_flush_plan,
    flush_memory_batches,
    flush_memory_database,
)


class FakeWorker:
    def __init__(self, *, fail_structured=False, fail_shared=False):
        self.fail_structured = fail_structured
        self.fail_shared = fail_shared
        self.closed = False
        self.calls = []

    async def extract_structured_memory(self, store, scope, *, min_turns=None):
        assert min_turns == 1
        self.calls.append(("structured", scope.conversation))
        if self.fail_structured:
            return False
        pending = store.pending_memory_extraction(scope, limit=4)
        if not pending:
            return False
        store.add_memory_item(
            scope,
            "사용자는 이전 대화를 기억해 달라고 했다.",
            kind="fact",
            disclosure="local",
            source_message_ids=(str(pending[-1]["message_id"]),),
        )
        store.save_memory_extraction_cursor(scope, int(pending[-1]["id"]))
        return True

    async def summarize_shared(self, store, scope, *, min_turns=None):
        assert min_turns == 1
        self.calls.append(("shared", scope.conversation))
        if self.fail_shared:
            return False
        pending = store.pending_shared(scope)
        if not pending:
            return False
        store.save_shared_summary(
            scope,
            pending[-1]["name"],
            "사용자는 공개 대화에서 지속적인 계획을 밝혔다.",
            int(pending[-1]["id"]),
        )
        return True

    async def close(self):
        self.closed = True


def _seed_pending(path):
    store = Store(str(path))
    dm = Scope(None, 123, 100)
    shared = Scope(44, 567, 101, True)
    store.add(dm, 1001, "remember my preference", "noted")
    store.add_shared_call(shared, 1002, "PublicUser", "here is my plan")
    store.close()
    return dm, shared


@pytest.mark.asyncio
async def test_flushes_one_turn_tails_and_succeeds_only_after_cursor_progress(tmp_path):
    path = tmp_path / "hina.sqlite3"
    dm, shared = _seed_pending(path)
    store = Store(str(path))
    worker = FakeWorker()
    try:
        before = build_memory_flush_plan(path)
        assert before.total_turns == 2
        structured, summaries, errors = await flush_memory_batches(store, worker)
        assert (structured, summaries, errors) == (1, 1, ())
        assert worker.calls == [
            ("structured", dm.conversation),
            ("shared", shared.conversation),
        ]
        assert store.memory_extraction_cursor(dm) > 0
        assert store.shared_summary(shared)[1] > 0
        assert len(store.memory_items(dm.user_id)) == 1
        assert build_memory_flush_plan(path).total_turns == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_does_not_skip_failed_batches_or_advance_cursors(tmp_path):
    path = tmp_path / "hina.sqlite3"
    dm, shared = _seed_pending(path)
    store = Store(str(path))
    try:
        worker = FakeWorker(fail_structured=True, fail_shared=True)
        structured, summaries, errors = await flush_memory_batches(store, worker)
        assert (structured, summaries) == (0, 0)
        assert len(errors) == 2
        assert store.memory_extraction_cursor(dm) == 0
        assert store.shared_summary(shared)[1] == 0
        assert build_memory_flush_plan(path).total_turns == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_respects_read_only_memory_modes(tmp_path):
    path = tmp_path / "hina.sqlite3"
    dm, shared = _seed_pending(path)
    store = Store(str(path))
    try:
        store.set_memory_mode_override("global", "read_only")
        structured, summaries, errors = await flush_memory_batches(store, FakeWorker())
        assert (structured, summaries) == (0, 0)
        assert len(errors) == 2
        assert store.memory_extraction_cursor(dm) == 0
        assert store.shared_summary(shared)[1] == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_full_flush_verifies_backup_before_memory_writes(tmp_path, monkeypatch):
    path = tmp_path / "hina.sqlite3"
    dm, shared = _seed_pending(path)
    monkeypatch.setattr(
        Settings, "load",
        classmethod(lambda cls: Settings(discord_token="test", db_path=str(path))),
    )
    worker = FakeWorker()
    result = await flush_memory_database(
        path,
        offline_confirmed=True,
        worker_factory=lambda _settings: worker,
    )
    assert result.success
    assert result.remaining.total_turns == 0
    assert result.structured_batches == 1
    assert result.shared_batches == 1
    assert result.backup_path.is_file()
    assert worker.closed
    with sqlite3.connect(result.backup_path) as original:
        assert original.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert original.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 0
        assert original.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        assert original.execute("SELECT COUNT(*) FROM shared_calls").fetchone()[0] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM shared_summaries").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    assert (dm.conversation, shared.conversation) == (
        "dm:100:channel:123:user:100",
        "guild:44:channel:567:user:101",
    )


@pytest.mark.asyncio
async def test_failed_flush_preserves_raw_history_for_retry(tmp_path, monkeypatch):
    path = tmp_path / "hina.sqlite3"
    _seed_pending(path)
    monkeypatch.setattr(
        Settings, "load",
        classmethod(lambda cls: Settings(discord_token="test", db_path=str(path))),
    )
    result = await flush_memory_database(
        path,
        offline_confirmed=True,
        worker_factory=lambda _settings: FakeWorker(fail_shared=True),
    )
    assert not result.success
    assert result.remaining.total_turns == 1
    assert result.backup_path.exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM shared_calls").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM shared_summaries").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_normal_shared_summary_threshold_unchanged_but_force_works():
    store = Store(":memory:")
    scope = Scope(1, 22, 33, True)
    store.add_shared_call(scope, 1234567, "User", "important repeated plans")
    fake = NS(status="completed", output_text="사용자의 공개 장기 계획")
    worker = type(
        "SummaryHarness",
        (MemorySummaryMixin,),
        {"_memory_request": lambda self, *a: _response(fake)},
    )()
    worker.settings = Settings(discord_token="test", summary_every=8)
    worker.usage = NS(routing_event=lambda *a, **kw: None)
    try:
        assert await worker.summarize_shared(store, scope) is False
        assert store.shared_summary(scope)[1] == 0
        assert await worker.summarize_shared(store, scope, min_turns=1) is True
        assert store.shared_summary(scope)[0] == "사용자의 공개 장기 계획"
        assert store.pending_shared(scope) == []
    finally:
        store.close()


async def _response(result):
    return result


def test_cli_flush_failure_never_resets_the_original_rows(tmp_path, monkeypatch):
    from hina_bot.tooling import flush_memory as flushing
    from hina_bot.tooling import reset_conversation_state as resetting

    path = tmp_path / "hina.sqlite3"
    _seed_pending(path)

    async def fail_flush(*_args, **_kwargs):
        return NS(
            success=False, backup_path=tmp_path / "safe-copy.sqlite3",
            structured_batches=0, shared_batches=0,
            errors=("structured scope: no committed progress",),
        )

    monkeypatch.setattr(flushing, "flush_memory_database", fail_flush)
    code = resetting.main([
        "--database", str(path), "--yes", "--offline-confirmed", "--flush-pending",
    ])
    assert code == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM shared_calls").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='conversation_reset_epochs'"
        ).fetchone()[0] == 0


def test_cli_flush_success_resets_only_after_pending_is_cleared(tmp_path, monkeypatch):
    from hina_bot.tooling import flush_memory as flushing
    from hina_bot.tooling import reset_conversation_state as resetting

    path = tmp_path / "hina.sqlite3"
    dm, shared = _seed_pending(path)

    async def success_flush(*_args, **_kwargs):
        store = Store(str(path))
        try:
            structured, summaries, errors = await flush_memory_batches(store, FakeWorker())
            assert not errors
            assert (structured, summaries) == (1, 1)
        finally:
            store.close()
        return NS(
            success=True, backup_path=tmp_path / "safe-copy.sqlite3",
            structured_batches=structured, shared_batches=summaries, errors=(),
        )

    monkeypatch.setattr(flushing, "flush_memory_database", success_flush)
    code = resetting.main([
        "--database", str(path), "--yes", "--offline-confirmed", "--flush-pending",
    ])
    assert code == 0
    with sqlite3.connect(path) as db:
        for table in ("turns", "shared_calls", "shared_summaries"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM conversation_reset_epochs"
        ).fetchone()[0] == 1
    assert dm.user_id == 100 and shared.guild_id == 44


def test_mutually_exclusive_flush_and_discard_pending_options(tmp_path):
    from hina_bot.tooling import reset_conversation_state as resetting

    path = tmp_path / "hina.sqlite3"
    _seed_pending(path)
    code = resetting.main([
        "--database", str(path), "--yes", "--offline-confirmed",
        "--flush-pending", "--discard-pending",
    ])
    assert code == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
