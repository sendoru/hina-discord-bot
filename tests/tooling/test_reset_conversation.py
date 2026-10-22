import sqlite3

import pytest

from hina_bot.core.db_schema import initialize_connection
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.repository import AdminRepository
from hina_bot.tooling.reset_conversation import (
    inspect_reset,
    main,
    reset_conversation,
)


def seed(path, *, pending=False):
    store = Store.create(str(path))
    dm = Scope(None, 10, 100)
    guild = Scope(1, 20, 100, True)
    store.add(dm, 101, "recent old DM", "old response", name="DM display name")
    store.add(guild, 102, "recent guild", "old response", name="Guild display name")
    with store.db:
        store._conversations.write_shared_call(guild, 103, "Guild display name", "shared text")
    memory = store.add_memory_item(
        dm,
        "사용자는 오렌지를 좋아한다.",
        kind="preference",
        disclosure="local",
        source_message_ids=("101",),
    )
    with store.db:
        store._conversations.write_shared_summary(guild, "Guild display name", "old summary", 1)
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
    assert result.archived == {"turns": 2, "failed_turns": 0}

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

    store = Store.create(str(path))
    try:
        assert counts(store.db) == {
            "turns": 0, "shared_calls": 0, "failed_turns": 0, "shared_summaries": 0,
        }
        assert tuple(store.db.execute(
            "SELECT * FROM memory_items WHERE id=?", (memory,)
        ).fetchone()) == item_before
        assert [tuple(row) for row in store.db.execute(
            "SELECT * FROM memory_extraction_cursors ORDER BY scope"
        ).fetchall()] == cursor_before
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


def test_debug_conversations_survive_multiple_resets_without_runtime_leak(tmp_path):
    path = tmp_path / "debug.sqlite3"
    dm, guild, _ = seed(path)
    store = Store.create(str(path))
    initialize_connection(store.db, ("admin",))
    try:
        with store.db:
            store.db.execute(
                "UPDATE turns SET turn_id='debug-success' WHERE message_id='101'"
            )
        store._conversations.add_failed_turn(
            dm, 888, "failed input", name="DM display name",
            reply="failed output", status="failed", stage="generation",
            reply_delivered=False, error_type="RuntimeError",
        )
        with store.db:
            store.db.execute(
                "UPDATE failed_turns SET turn_id='debug-failure' "
                "WHERE message_id='888'"
            )
    finally:
        store.close()

    _, first = reset_conversation(path, offline_confirmed=True)
    assert first.archived == {"turns": 2, "failed_turns": 1}
    with sqlite3.connect(path) as db:
        assert counts(db) == dict.fromkeys(counts(db), 0)
        assert db.execute(
            "SELECT COUNT(*) FROM conversation_debug_archive"
        ).fetchone()[0] == 3

    # Dashboard still has searchable, trace-linked transcripts; runtime is empty.
    repo = AdminRepository(path)
    assert repo.count_conversations() == 3
    assert repo.count_conversations(query="failed input") == 1
    assert repo.turn_for_trace("debug-success")["content"] == "recent old DM"
    assert repo.turn_for_trace("debug-failure")["record_type"] == "failed"
    assert repo.turn_by_id(1)["content"] == "recent old DM"
    assert [x["message_id"] for x in repo.turns_for_message_ids(["101"])] == ["101"]
    assert repo.turn_context(1)[0]["content"] == "recent old DM"

    store = Store.create(str(path))
    try:
        assert store.history(dm) == []
        assert store.history(guild) == []
        assert store.pending_memory_extraction(dm) == []
        assert store._conversations.public_candidates(100, 1) == []
        store.add(dm, 999, "new message", "new reply")
        store.save_memory_extraction_cursor(dm, store.history(dm)[-1]["id"])
    finally:
        store.close()

    _, second = reset_conversation(path, offline_confirmed=True)
    assert second.archived == {"turns": 1, "failed_turns": 0}
    repo = AdminRepository(path)
    assert repo.count_conversations() == 4
    assert repo.count_conversations(query="recent old DM") == 1
    assert repo.turn_for_trace("debug-failure")["content"] == "failed input"
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM conversation_debug_archive"
        ).fetchone()[0] == 4


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


def test_change_after_backup_is_rejected_before_deletion(tmp_path, monkeypatch):
    import hina_bot.tooling.reset_conversation as mod

    path = tmp_path / "changed.sqlite3"
    dm, _, _ = seed(path)
    backup = mod.backup_sqlite

    def backup_then_write(*args, **kwargs):
        result = backup(*args, **kwargs)
        writer = Store.create(str(path))
        try:
            writer.add(dm, 999, "new pending source", "reply")
        finally:
            writer.close()
        return result

    monkeypatch.setattr(mod, "backup_sqlite", backup_then_write)
    with pytest.raises(RuntimeError, match="Database changed since backup"):
        mod.reset_conversation(path, offline_confirmed=True)
    with sqlite3.connect(path) as db:
        assert counts(db) == {
            "turns": 3, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1,
        }
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='conversation_reset_epochs'"
        ).fetchone() is None


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


def test_legacy_reset_epoch_table_missing_backup_hash_is_upgraded(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    seed(path)
    # A deployed DB may already have epochs from an older schema.
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE conversation_reset_epochs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reset_at TEXT NOT NULL,
                cutoff_message_id INTEGER NOT NULL
            )"""
        )
        db.execute(
            "INSERT INTO conversation_reset_epochs(reset_at,cutoff_message_id) "
            "VALUES (?,?)",
            ("2026-09-01T00:00:00+00:00", 123456789),
        )

    plan, result = reset_conversation(path, offline_confirmed=True)
    assert plan.ready
    assert result.reset_id == 2
    assert result.deleted == {
        "turns": 2, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1,
    }
    with sqlite3.connect(path) as db:
        assert counts(db) == {
            "turns": 0, "shared_calls": 0, "failed_turns": 0, "shared_summaries": 0,
        }
        assert db.execute(
            "SELECT reset_at,cutoff_message_id,backup_sha256 "
            "FROM conversation_reset_epochs WHERE id=1"
        ).fetchone() == ("2026-09-01T00:00:00+00:00", 123456789, "")
        assert db.execute(
            "SELECT backup_sha256 FROM conversation_reset_epochs WHERE id=2"
        ).fetchone()[0] == result.backup.sha256
        assert db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 1

    # The verified pre-reset backup has the legacy schema and all conversations.
    with sqlite3.connect(result.backup.path) as snapshot:
        assert counts(snapshot)["turns"] == 2
        assert "backup_sha256" not in {
            row[1] for row in snapshot.execute(
                "PRAGMA table_info(conversation_reset_epochs)"
            )
        }


def test_invalid_reset_epoch_schema_rolls_back_all_deletions(tmp_path):
    path = tmp_path / "invalid.sqlite3"
    seed(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE conversation_reset_epochs (id INTEGER PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="Unsupported conversation_reset_epochs"):
        reset_conversation(path, offline_confirmed=True)
    with sqlite3.connect(path) as db:
        assert counts(db) == {
            "turns": 2, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1,
        }


@pytest.mark.parametrize("has_backup_sha256", [False, True])
def test_legacy_discard_counters_are_removed_by_atomic_reset(tmp_path, has_backup_sha256):
    path = tmp_path / "legacy-counter.sqlite3"
    seed(path)
    backup_column = ", backup_sha256 TEXT NOT NULL" if has_backup_sha256 else ""
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE conversation_reset_epochs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "reset_at TEXT NOT NULL,"
            "cutoff_message_id INTEGER NOT NULL,"
            "discarded_structured_turns INTEGER NOT NULL,"
            "discarded_shared_turns INTEGER NOT NULL"
            + backup_column + ")"
        )
        previous_columns = (
            "reset_at,cutoff_message_id,discarded_structured_turns,"
            "discarded_shared_turns"
            + (",backup_sha256" if has_backup_sha256 else "")
        )
        previous_values = (
            "2026-09-01T00:00:00+00:00", 123456789, 7, 9
        ) + (("legacy-hash",) if has_backup_sha256 else ())
        db.execute(
            "INSERT INTO conversation_reset_epochs (" + previous_columns
            + ") VALUES (" + ",".join("?" for _ in previous_values) + ")",
            previous_values,
        )

    _, result = reset_conversation(path, offline_confirmed=True)
    assert result.reset_id == 2
    with sqlite3.connect(path) as db:
        assert counts(db) == {
            "turns": 0, "shared_calls": 0, "failed_turns": 0, "shared_summaries": 0,
        }
        assert {
            row[1] for row in db.execute(
                "PRAGMA table_info(conversation_reset_epochs)"
            )
        } == {"id", "reset_at", "cutoff_message_id", "backup_sha256"}
        rows = db.execute(
            "SELECT id,cutoff_message_id,backup_sha256 "
            "FROM conversation_reset_epochs ORDER BY id"
        ).fetchall()
        assert rows[0] == (
            1, 123456789, "legacy-hash" if has_backup_sha256 else "",
        )
        assert rows[1] == (2, rows[1][1], result.backup.sha256)
        assert rows[1][1] > rows[0][1]
        assert db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 1
    with sqlite3.connect(result.backup.path) as backup:
        assert counts(backup)["turns"] == 2
        assert backup.execute(
            "SELECT discarded_structured_turns,discarded_shared_turns "
            "FROM conversation_reset_epochs"
        ).fetchone() == (7, 9)


def test_unknown_required_legacy_epoch_column_refuses_without_data_loss(tmp_path):
    path = tmp_path / "legacy-unknown.sqlite3"
    seed(path)
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE conversation_reset_epochs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reset_at TEXT NOT NULL,
                cutoff_message_id INTEGER NOT NULL,
                unknown_required_field INTEGER NOT NULL
            )"""
        )
    with pytest.raises(RuntimeError, match="unknown_required_field"):
        reset_conversation(path, offline_confirmed=True)
    with sqlite3.connect(path) as db:
        assert counts(db) == {
            "turns": 2, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1,
        }
        assert "backup_sha256" not in {
            row[1] for row in db.execute(
                "PRAGMA table_info(conversation_reset_epochs)"
            )
        }



def test_discard_pending_keeps_backup_and_memory_without_extraction(tmp_path):
    path = tmp_path / "pending.sqlite3"
    dm, guild, memory = seed(path, pending=True)
    with sqlite3.connect(path) as db:
        original_memory = db.execute(
            "SELECT * FROM memory_items WHERE id=?", (memory,)
        ).fetchone()
        cursors_before = db.execute(
            "SELECT * FROM memory_extraction_cursors ORDER BY scope"
        ).fetchall()
        sequence_before = db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='turns'"
        ).fetchone()[0]

    plan, result = reset_conversation(
        path, offline_confirmed=True, discard_pending=True,
    )
    assert not plan.ready
    assert (plan.pending_turns, plan.pending_scopes) == (2, 2)
    assert result.backup.path.is_file()
    assert result.archived == {"turns": 2, "failed_turns": 0}
    assert result.deleted == {
        "turns": 2, "shared_calls": 1, "failed_turns": 0, "shared_summaries": 1,
    }
    with sqlite3.connect(result.backup.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert counts(db)["turns"] == 2
        assert db.execute(
            "SELECT content FROM turns WHERE message_id='101'"
        ).fetchone()[0] == "recent old DM"

    store = Store.create(str(path))
    try:
        assert counts(store.db) == dict.fromkeys(counts(store.db), 0)
        assert tuple(store.db.execute(
            "SELECT * FROM memory_items WHERE id=?", (memory,)
        ).fetchone()) == original_memory
        assert [tuple(row) for row in store.db.execute(
            "SELECT * FROM memory_extraction_cursors ORDER BY scope"
        ).fetchall()] == cursors_before
        assert store.db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='turns'"
        ).fetchone()[0] == sequence_before
        assert store.db.execute(
            "SELECT COUNT(*) FROM conversation_debug_archive"
        ).fetchone()[0] == 2
        assert store.history(dm) == []
        assert store.history(guild) == []
        assert store.pending_memory_extraction(dm) == []
        assert store.note(dm.user_note) == "important custom user note"
        assert store.user_name(dm) == "DM display name"
        store.add(dm, 104, "new message", "new response")
        assert [row["message_id"] for row in store.pending_memory_extraction(dm)] == [
            "104",
        ]
    finally:
        store.close()


def test_discard_pending_cli_is_explicit_and_requires_offline(tmp_path, capsys):
    path = tmp_path / "pending-cli.sqlite3"
    seed(path, pending=True)
    assert main(["--database", str(path), "--dry-run"]) == 0
    assert "Status: BLOCKED" in capsys.readouterr().out
    assert main(["--database", str(path), "--dry-run", "--discard-pending"]) == 0
    preview = capsys.readouterr().out
    assert "2 turns / 2 scopes" in preview
    assert "will be discarded without flush" in preview
    assert not (tmp_path / "backups").exists()
    assert main(["--database", str(path), "--yes", "--discard-pending"]) == 2
    assert not (tmp_path / "backups").exists()
    assert main([
        "--database", str(path), "--yes", "--offline-confirmed",
    ]) == 2
    assert not (tmp_path / "backups").exists()
    assert main([
        "--database", str(path), "--yes", "--offline-confirmed",
        "--discard-pending",
    ]) == 0
    output = capsys.readouterr().out
    assert "discarded_pending=2" in output


def test_discard_pending_rechecks_plan_after_backup(tmp_path, monkeypatch):
    import hina_bot.tooling.reset_conversation as mod

    path = tmp_path / "pending-changed.sqlite3"
    dm, _, _ = seed(path, pending=True)
    original_backup = mod.backup_sqlite

    def backup_then_write(*args, **kwargs):
        result = original_backup(*args, **kwargs)
        store = Store.create(str(path))
        try:
            store.add(dm, 999, "arrived after backup", "reply")
        finally:
            store.close()
        return result

    monkeypatch.setattr(mod, "backup_sqlite", backup_then_write)
    with pytest.raises(RuntimeError, match="Database changed since backup"):
        mod.reset_conversation(
            path, offline_confirmed=True, discard_pending=True,
        )
    with sqlite3.connect(path) as db:
        assert counts(db)["turns"] == 3
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='conversation_reset_epochs'"
        ).fetchone() is None
