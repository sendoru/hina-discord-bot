"""Regression coverage for cleaning the original exploratory epoch schema."""

import sqlite3

import pytest

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.migrate_conversation_reset_epochs import (
    CURRENT_COLUMNS,
    epoch_columns,
    main,
    migrate_epoch_schema,
)


def _legacy_database(path, *, has_hash=False):
    store = Store.create(str(path))
    scope = Scope(None, 10, 100)
    store.add(scope, 101, "original conversation", "original reply")
    store.add_memory_item(
        scope, "사용자는 오렌지를 좋아한다.", kind="preference",
        disclosure="local", source_message_ids=("101",),
    )
    store.close()
    with sqlite3.connect(path) as db:
        backup = ", backup_sha256 TEXT NOT NULL" if has_hash else ""
        db.execute(
            "CREATE TABLE conversation_reset_epochs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "reset_at TEXT NOT NULL,"
            "cutoff_message_id INTEGER NOT NULL,"
            "discarded_structured_turns INTEGER NOT NULL,"
            "discarded_shared_turns INTEGER NOT NULL"
            + backup + ")"
        )
        columns = (
            "reset_at,cutoff_message_id,discarded_structured_turns,"
            "discarded_shared_turns"
            + (",backup_sha256" if has_hash else "")
        )
        for row in (
            ("2026-09-01", 123, 7, 8),
            ("2026-09-02", 456, 3, 4),
        ):
            values = row + (("existing-hash",) if has_hash else ())
            db.execute(
                "INSERT INTO conversation_reset_epochs (" + columns + ") "
                "VALUES (" + ",".join("?" for _ in values) + ")",
                values,
            )
        # Retain AUTOINCREMENT high watermark even when the last row is gone.
        db.execute("DELETE FROM conversation_reset_epochs WHERE id=2")


@pytest.mark.parametrize("has_hash", [False, True])
def test_migrate_original_epoch_table_preserves_backup_history_and_all_other_data(
    tmp_path, has_hash
):
    path = tmp_path / "hina.sqlite3"
    _legacy_database(path, has_hash=has_hash)

    assert main(["--database", str(path), "--dry-run"]) == 0
    with sqlite3.connect(path) as db:
        assert "discarded_shared_turns" in epoch_columns(db)

    changed, backup_info = migrate_epoch_schema(path, offline_confirmed=True)
    assert changed and backup_info is not None
    assert backup_info.path.is_file()
    with sqlite3.connect(path) as db:
        assert epoch_columns(db) == CURRENT_COLUMNS
        assert db.execute(
            "SELECT id,reset_at,cutoff_message_id,backup_sha256 "
            "FROM conversation_reset_epochs"
        ).fetchall() == [
            (1, "2026-09-01", 123, "existing-hash" if has_hash else ""),
        ]
        assert db.execute(
            "SELECT seq FROM sqlite_sequence "
            "WHERE name='conversation_reset_epochs'"
        ).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 1
    with sqlite3.connect(backup_info.path) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "discarded_shared_turns" in epoch_columns(backup)
        assert backup.execute(
            "SELECT discarded_structured_turns,discarded_shared_turns "
            "FROM conversation_reset_epochs"
        ).fetchone() == (7, 8)

    again, another_backup = migrate_epoch_schema(path, offline_confirmed=True)
    assert again is False and another_backup is None
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO conversation_reset_epochs("
            "reset_at,cutoff_message_id,backup_sha256) VALUES (?,?,?)",
            ("2026-10-14", 789, "latest-hash"),
        )
        assert db.execute(
            "SELECT MAX(id) FROM conversation_reset_epochs"
        ).fetchone()[0] == 3


def test_migration_rejects_unexpected_columns_without_data_loss(tmp_path):
    path = tmp_path / "hina.sqlite3"
    _legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "ALTER TABLE conversation_reset_epochs "
            "ADD COLUMN unexpected_required INTEGER NOT NULL DEFAULT 0"
        )
    with pytest.raises(RuntimeError, match="unexpected_required"):
        migrate_epoch_schema(path, offline_confirmed=True)
    with sqlite3.connect(path) as db:
        assert "discarded_shared_turns" in epoch_columns(db)
        assert "unexpected_required" in epoch_columns(db)
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1


def test_schema_change_after_backup_refuses_migration(tmp_path, monkeypatch):
    from hina_bot.tooling import migrate_conversation_reset_epochs as mod

    path = tmp_path / "changed.sqlite3"
    _legacy_database(path)
    backup = mod.backup_sqlite

    def backup_then_change(*args, **kwargs):
        result = backup(*args, **kwargs)
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE conversation_reset_epochs ADD COLUMN unexpected TEXT")
        return result

    monkeypatch.setattr(mod, "backup_sqlite", backup_then_change)
    with pytest.raises(RuntimeError, match="Epoch schema changed since backup"):
        mod.migrate_epoch_schema(path, offline_confirmed=True)
    with sqlite3.connect(path) as db:
        assert "discarded_shared_turns" in epoch_columns(db)
        assert "backup_sha256" not in epoch_columns(db)
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1


def test_failed_drop_column_rolls_back_added_backup_column(tmp_path):
    path = tmp_path / "hina.sqlite3"
    _legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE INDEX epoch_legacy_idx "
            "ON conversation_reset_epochs(discarded_shared_turns)"
        )
    with pytest.raises(sqlite3.OperationalError):
        migrate_epoch_schema(path, offline_confirmed=True)
    with sqlite3.connect(path) as db:
        assert "backup_sha256" not in epoch_columns(db)
        assert "discarded_structured_turns" in epoch_columns(db)
        assert "discarded_shared_turns" in epoch_columns(db)
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1


def test_cli_requires_confirmation_and_preserves_unchanged_database(tmp_path):
    path = tmp_path / "hina.sqlite3"
    _legacy_database(path)
    assert main(["--database", str(path), "--yes"]) == 2
    with sqlite3.connect(path) as db:
        assert "discarded_shared_turns" in epoch_columns(db)
    assert main([
        "--database", str(path), "--yes", "--offline-confirmed",
    ]) == 0
    with sqlite3.connect(path) as db:
        assert epoch_columns(db) == CURRENT_COLUMNS
