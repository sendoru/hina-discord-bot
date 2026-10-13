import sqlite3

import pytest

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.reset_conversation import (
    inspect_reset,
    main,
    reset_conversation,
)


def seed(path, *, pending=False):
    store = Store(str(path))
    dm = Scope(None, 10, 100)
    guild = Scope(1, 20, 100, True)
    store.add(dm, 101, "recent old DM", "old response", name="DM display name")
    store.add(guild, 102, "recent guild", "old response", name="Guild display name")
    store.add_shared_call(guild, 103, "Guild display name", "shared text")
    memory = store.add_memory_item(
        dm,
        "사용자는 오렌지를 좋아한다.",
        kind="preference",
        disclosure="local",
        source_message_ids=("101",),
    )
    store.save_shared_summary(guild, "Guild display name", "old summary", 1)
    if not pending:
        store.save_memory_extraction_cursor(dm, store.history(dm)[-1]["id"])
        store.save_memory_extraction_cursor(guild, store.history(guild)[-1]["id"])
    store.set_note(dm.user_note, "important custom user note")
    store.set_memory_mode_override("global", "normal")
    store.close()
    return dm, guild, memory


def counts(db):
    return {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("turns", "shared_calls", "failed_turns", "shared_summaries")
    }


def test_backup_and_reset_preserves_user_memory_and_settings(tmp_path):
    path = tmp_path / "test.sqlite3"
    dm, guild, memory = seed(path)
    telemetry = tmp_path / "usage.jsonl"
    telemetry.write_text("old trace\n", encoding="utf-8")
    before = sqlite3.connect(path)
    try:
        item_before = before.execute(
            "SELECT * FROM memory_items WHERE id=?", (memory,)
        ).fetchone()
        cursor_before = before.execute(
            "SELECT * FROM memory_extraction_cursors ORDER BY scope"
        ).fetchall()
        dm_seq = before.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='turns'"
        ).fetchone()[0]
    finally:
        before.close()

    plan, result = reset_conversation(path, offline_confirmed=True)
    assert plan.ready
    assert result.backup.path.is_file()
    assert result.backup.path.stat().st_mode & 0o077 == 0
    assert result.deleted == {
        "turns": 2, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1,
    }

    # Backup B is a usable SQLite database, not just a file copy of WAL-less pages.
    backup = sqlite3.connect(result.backup.path)
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert counts(backup) == {
            "turns": 2, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1,
        }
        assert backup.execute(
            "SELECT content FROM turns WHERE message_id='101'"
        ).fetchone()[0] == "recent old DM"
    finally:
        backup.close()

    store = Store(str(path))
    try:
        assert counts(store.db) == {
            "turns": 0, "shared_calls": 0, "failed_turns": 0, "shared_summaries": 0,
        }
        assert store.db.execute(
            "SELECT * FROM memory_items WHERE id=?", (memory,)
        ).fetchone() == item_before
        assert store.db.execute(
            "SELECT * FROM memory_extraction_cursors ORDER BY scope"
        ).fetchall() == cursor_before
        assert store.note(dm.user_note) == "important custom user note"
        assert store.memory_mode(dm) == "normal"
        assert store.user_name(dm) == "DM display name"
        assert store.user_name(guild) == "Guild display name"
        assert store.latest_conversation_reset_cutoff() > 0
        assert store.db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='turns'"
        ).fetchone()[0] == dm_seq
        store.add(dm, 104, "new post reset turn", "fresh reply", name="DM display name")
        assert store.pending_memory_extraction(dm)[0]["message_id"] == "104"
        assert store.history(dm)[0]["content"] == "new post reset turn"
    finally:
        store.close()
    assert telemetry.read_text(encoding="utf-8") == "old trace\n"


def test_pending_refuses_before_backup_and_deletion(tmp_path):
    path = tmp_path / "test.sqlite3"
    seed(path, pending=True)
    plan = inspect_reset(path)
    assert plan.pending_turns == 2
    assert plan.pending_scopes == 2
    with pytest.raises(RuntimeError, match="Unextracted"):
        reset_conversation(path, offline_confirmed=True)
    assert not (tmp_path / "backups").exists()
    with sqlite3.connect(path) as db:
        assert counts(db)["turns"] == 2


def test_missing_offline_confirmation_refuses_before_backup(tmp_path):
    path = tmp_path / "test.sqlite3"
    seed(path)
    with pytest.raises(ValueError, match="offline-confirmed"):
        reset_conversation(path)
    assert not (tmp_path / "backups").exists()
    assert main(["--database", str(path), "--dry-run"]) == 0
    assert main(["--database", str(path), "--yes"]) == 2


def test_failed_sql_mutation_rolls_back_all_conversation_tables(tmp_path, monkeypatch):
    import hina_bot.tooling.reset_conversation as mod

    path = tmp_path / "test.sqlite3"
    seed(path)
    original = mod._remember_names

    def fails_after_write(db):
        original(db)
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(mod, "_remember_names", fails_after_write)
    with pytest.raises(RuntimeError, match="injected"):
        reset_conversation(path, offline_confirmed=True)
    with sqlite3.connect(path) as db:
        assert counts(db) == {
            "turns": 2, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1,
        }
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='conversation_reset_epochs'"
        ).fetchone()[0] == 0


def test_backup_permission_failure_preserves_everything(tmp_path):
    path = tmp_path / "test.sqlite3"
    seed(path)
    backup_dir = tmp_path / "insecure"
    backup_dir.mkdir()
    backup_dir.chmod(0o755)
    with pytest.raises(PermissionError):
        reset_conversation(
            path, offline_confirmed=True, backup_dir=backup_dir
        )
    with sqlite3.connect(path) as db:
        assert counts(db)["turns"] == 2


def test_repeat_reset_adds_epoch_without_reusing_old_sequence(tmp_path):
    path = tmp_path / "test.sqlite3"
    seed(path)
    _, first = reset_conversation(path, offline_confirmed=True)
    _, second = reset_conversation(path, offline_confirmed=True)
    assert second.reset_id == first.reset_id + 1
    assert second.backup.path != first.backup.path
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM conversation_reset_epochs"
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='turns'"
        ).fetchone()[0] == 2
