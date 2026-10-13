import sqlite3

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.flush_pending_memory import (
    flush_pending,
    inspect_pending,
    main,
    run_flush,
)


class FakeWorker:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail
        self.closed = False

    async def extract_structured_memory(self, store, scope, *, min_turns=None):
        self.calls.append((scope.conversation, min_turns))
        if self.fail:
            return False
        pending = store.pending_memory_extraction(scope, limit=4)
        if not pending:
            return False
        store.save_memory_extraction_cursor(scope, pending[-1]["id"])
        return True

    async def close(self):
        self.closed = True


def _seed(path, count=5):
    store = Store(str(path), history_turns=12)
    scope = Scope(None, 456, 123)
    for number in range(count):
        store.add(scope, 1000 + number, f"user-{number}", "reply")
    store.close()
    return scope


@pytest.mark.asyncio
async def test_one_turn_tail_and_multiple_batches(tmp_path):
    path = tmp_path / "test.sqlite3"
    scope = _seed(path, 5)
    store = Store(str(path))
    worker = FakeWorker()
    try:
        assert inspect_pending(path)[0].count == 5
        batches, errors = await flush_pending(store, worker)
        assert (batches, errors) == (2, ())
        assert worker.calls == [(scope.conversation, 1)] * 2
        assert inspect_pending(path) == ()
        assert store.memory_extraction_cursor(scope) == store.history(scope)[-1]["id"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_failure_keeps_raw_turns_and_cursor(tmp_path):
    path = tmp_path / "test.sqlite3"
    scope = _seed(path, 1)
    store = Store(str(path))
    try:
        batches, errors = await flush_pending(store, FakeWorker(fail=True))
        assert batches == 0 and len(errors) == 1
        assert store.memory_extraction_cursor(scope) == 0
        assert len(store.pending_memory_extraction(scope)) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_read_only_mode_cannot_be_bypassed(tmp_path):
    path = tmp_path / "test.sqlite3"
    scope = _seed(path, 1)
    store = Store(str(path))
    try:
        store.set_memory_mode_override("global", "read_only")
        worker = FakeWorker()
        batches, errors = await flush_pending(store, worker)
        assert batches == 0 and len(errors) == 1
        assert worker.calls == []
        assert store.memory_extraction_cursor(scope) == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_full_flush_backs_up_before_writing_and_closes_worker(tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite3"
    scope = _seed(path, 1)
    monkeypatch.setattr(
        Settings, "load",
        classmethod(lambda cls: Settings(discord_token="test", db_path=str(path))),
    )
    worker = FakeWorker()
    result = await run_flush(path, worker_factory=lambda settings: worker)
    assert result.completed and result.batches == 1
    assert worker.closed
    assert result.backup.path.is_file()
    assert result.backup.path.stat().st_mode & 0o077 == 0
    with sqlite3.connect(result.backup.path) as old:
        assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert old.execute(
            "SELECT COUNT(*) FROM memory_extraction_cursors"
        ).fetchone()[0] == 0
        assert old.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    with sqlite3.connect(path) as new:
        assert new.execute(
            "SELECT through_id FROM memory_extraction_cursors WHERE scope=?",
            (scope.conversation,),
        ).fetchone()[0] > 0
        assert new.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1


def test_dry_run_never_writes_or_initializes_db(tmp_path):
    path = tmp_path / "test.sqlite3"
    _seed(path, 1)
    assert main(["--database", str(path), "--dry-run"]) == 0
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM memory_extraction_batches").fetchone()[0] == 0
    assert not (tmp_path / "backups").exists()


def test_missing_offline_confirmation_never_mutates(tmp_path):
    path = tmp_path / "test.sqlite3"
    _seed(path, 1)
    assert main(["--database", str(path), "--yes"]) == 2
    assert not (tmp_path / "backups").exists()


def test_backup_failure_does_not_flush(tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite3"
    scope = _seed(path, 1)
    monkeypatch.setattr(
        Settings, "load",
        classmethod(lambda cls: Settings(discord_token="test", db_path=str(path))),
    )
    blocked = tmp_path / "backups"
    blocked.mkdir()
    blocked.chmod(0o755)
    with pytest.raises(PermissionError):
        import asyncio

        asyncio.run(run_flush(path))
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM memory_extraction_cursors WHERE scope=?",
            (scope.conversation,),
        ).fetchone()[0] == 0
