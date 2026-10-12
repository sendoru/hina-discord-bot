import hashlib
import sqlite3

import pytest

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.reset_analysis_data import UnsafeAnalysisReset
from hina_bot.tooling.reset_conversation_state import (
    build_conversation_reset_plan,
    reset_conversation_state,
)


def _seed(path, *, pending=False):
    store = Store(str(path))
    dm = Scope(None, 10, 100)
    guild = Scope(1, 20, 100, True)

    store.add(dm, 1, "private user text", "old defensive reply", name="User")
    old_turn = int(store.history(dm)[-1]["id"])
    memory_id = store.add_memory_item(
        dm,
        "사용자는 고양이를 좋아한다.",
        kind="preference",
        disclosure="local",
        source_message_ids=("1",),
    )
    store.add_shared_call(guild, 2, "User", "public question")
    old_shared = int(
        store.db.execute(
            "SELECT MAX(id) FROM shared_calls"
        ).fetchone()[0]
    )
    if not pending:
        store.save_memory_extraction_cursor(dm, old_turn)
        store.save_shared_summary(guild, "User", "old defensive summary", old_shared)
    else:
        store.save_shared_summary(guild, "User", "stale prior summary", 0)

    with store.db:
        store.db.execute(
            "INSERT OR REPLACE INTO memory_modes(scope,mode) VALUES (?,?)",
            ("global", "normal"),
        )
        store.db.execute(
            "INSERT OR REPLACE INTO notes(scope,text) VALUES (?,?)",
            (dm.user_note, "important manual user note"),
        )
    store.close()
    return dm, guild, memory_id, old_turn, old_shared


def _counts(db):
    return {
        table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("turns", "shared_calls", "failed_turns", "shared_summaries")
    }


def test_reset_preserves_memory_and_settings_and_backups_all_original_rows(tmp_path):
    path = tmp_path / "hina.sqlite3"
    dm, _guild, memory_id, old_turn, old_shared = _seed(path)
    log = tmp_path / "events.jsonl"
    log.write_text("old trace\n", encoding="utf-8")

    plan = build_conversation_reset_plan(path)
    assert plan.safe
    assert plan.removed_rows["shared_summaries"] == 1

    before = sqlite3.connect(path)
    try:
        memory_before = before.execute(
            "SELECT * FROM memory_items WHERE id=?", (memory_id,)
        ).fetchone()
        cursor_before = before.execute(
            "SELECT * FROM memory_extraction_cursors"
        ).fetchall()
    finally:
        before.close()

    observed, result = reset_conversation_state(
        path, apply=True, offline_confirmed=True
    )
    assert observed == plan
    assert result is not None
    assert result.backup_path.is_file()
    assert result.backup_path != path
    assert result.backup_path.stat().st_mode & 0o077 == 0
    assert hashlib.sha256(result.backup_path.read_bytes()).hexdigest() == result.backup_sha256
    assert result.deleted_rows == {
        "turns": 1, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1
    }
    assert log.read_text(encoding="utf-8") == "old trace\n"

    backup = sqlite3.connect(result.backup_path)
    try:
        assert _counts(backup) == {
            "turns": 1, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1
        }
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute(
            "SELECT content FROM turns"
        ).fetchone()[0] == "private user text"
        assert backup.execute(
            "SELECT text FROM shared_summaries"
        ).fetchone()[0] == "old defensive summary"
    finally:
        backup.close()

    db = sqlite3.connect(path)
    try:
        assert all(count == 0 for count in _counts(db).values())
        assert db.execute(
            "SELECT * FROM memory_items WHERE id=?", (memory_id,)
        ).fetchone() == memory_before
        assert db.execute(
            "SELECT * FROM memory_extraction_cursors"
        ).fetchall() == cursor_before
        assert db.execute(
            "SELECT text FROM notes WHERE scope=?", (dm.user_note,)
        ).fetchone()[0] == "important manual user note"
        assert db.execute("SELECT mode FROM memory_modes WHERE scope='global'").fetchone()[0] == "normal"
        row = db.execute(
            "SELECT cutoff_message_id,discarded_structured_turns,"
            "discarded_shared_turns FROM conversation_reset_epochs"
        ).fetchone()
        assert row == (result.cutoff_message_id, 0, 0)
        assert result.cutoff_message_id > 0
        assert db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='turns'"
        ).fetchone()[0] == old_turn
        assert db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='shared_calls'"
        ).fetchone()[0] == old_shared
    finally:
        db.close()

    # Newly persisted turns must never silently fall behind a preserved extraction cursor.
    reopened = Store(str(path))
    reopened.add(dm, 3, "fresh user text", "fresh answer")
    assert int(reopened.history(dm)[-1]["id"]) > old_turn
    assert reopened.pending_memory_extraction(dm)[0]["content"] == "fresh user text"
    reopened.close()


def test_pending_blocks_by_default_with_no_snapshot_or_deletion(tmp_path):
    path = tmp_path / "hina.sqlite3"
    _seed(path, pending=True)
    plan = build_conversation_reset_plan(path)
    pending = {row.kind: row.turns for row in plan.pending}
    assert pending["structured_memory"] == 1
    assert pending["shared_summary"] == 1

    with pytest.raises(UnsafeAnalysisReset):
        reset_conversation_state(path, apply=True, offline_confirmed=True)

    assert not (tmp_path / "backups").exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM shared_summaries").fetchone()[0] == 1


def test_explicit_pending_discard_only_after_backup(tmp_path):
    path = tmp_path / "hina.sqlite3"
    _seed(path, pending=True)
    plan, result = reset_conversation_state(
        path, apply=True, offline_confirmed=True, discard_pending=True
    )
    assert not plan.safe
    assert result is not None
    with sqlite3.connect(result.backup_path) as backup:
        assert backup.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    with sqlite3.connect(path) as db:
        assert _counts(db)["turns"] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 1
        row = db.execute(
            "SELECT discarded_structured_turns,discarded_shared_turns "
            "FROM conversation_reset_epochs"
        ).fetchone()
        assert row == (1, 1)


def test_offline_confirmation_and_dry_run_do_not_modify_data(tmp_path):
    path = tmp_path / "hina.sqlite3"
    _seed(path)
    plan, result = reset_conversation_state(path)
    assert plan.safe and result is None
    with pytest.raises(ValueError, match="offline-confirmed"):
        reset_conversation_state(path, apply=True)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    assert not (tmp_path / "backups").exists()


def test_backup_failure_prevents_reset_and_preserves_original_rows(tmp_path):
    path = tmp_path / "hina.sqlite3"
    _seed(path)
    invalid_backup_dir = tmp_path / "occupied"
    invalid_backup_dir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        reset_conversation_state(
            path, apply=True, offline_confirmed=True, backup_dir=invalid_backup_dir
        )
    with sqlite3.connect(path) as db:
        assert _counts(db)["turns"] == 1
        assert _counts(db)["shared_summaries"] == 1


def test_repeat_reset_records_monotonic_cutoffs_and_preserves_backups(tmp_path):
    path = tmp_path / "hina.sqlite3"
    _dm, _, _, old_turn, _ = _seed(path)
    _, a = reset_conversation_state(path, apply=True, offline_confirmed=True)
    _, b = reset_conversation_state(path, apply=True, offline_confirmed=True)
    assert a is not None and b is not None
    assert a.reset_id == 1
    assert b.reset_id == 2
    assert a.backup_path != b.backup_path
    assert a.backup_path.is_file() and b.backup_path.is_file()
    assert b.cutoff_message_id >= a.cutoff_message_id
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM conversation_reset_epochs").fetchone()[0] == 2
        assert db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='turns'"
        ).fetchone()[0] == old_turn
