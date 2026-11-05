import json

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.maintenance_backup import backup_sqlite, database_signature
from hina_bot.tooling.retention_report import main, retention_report


def test_report_is_readonly_content_free_and_backup_restores_complete_state(tmp_path, capsys):
    path = tmp_path / "private.sqlite3"
    store = Store.create(path, history_turns=2, raw_retention_turns=3)
    scope = Scope(1, 10, 100, True)
    try:
        for number in range(1, 16):
            store.record_completed_turn(scope, number, "private-secret", "private-reply")
        store.save_memory_extraction_cursor(scope, 2)
        store.set_user_memory_mode(100, "read_only")
        store.add_memory_item(scope, "private-memory", kind="fact", disclosure="local",
                              source_message_ids=("1",))
        before = database_signature(store.db)
        result = retention_report(path, raw_retention_turns=3)
        turns = result["tables"]["turns"]
        assert (turns["rows"], turns["pending"], turns["pending_write_disabled"]) == (15, 13, 13)
        assert turns["over_target_rows"] == 12
        assert result["tables"]["shared_calls"]["pending"] == 15
        assert result["tables"]["failed_turns"]["pending"] == 0
        main(["--database", str(path), "--raw-retention-turns", "3"])
        output = capsys.readouterr().out
        assert json.loads(output)["tables"] == result["tables"]
        assert "private-secret" not in output
        assert scope.conversation not in output
        assert database_signature(store.db) == before
        backup = backup_sqlite(path, backup_dir=tmp_path / "backups")
        restored = Store.open_existing(backup.path, history_turns=2, raw_retention_turns=3)
        try:
            assert database_signature(restored.db) == before
            assert restored.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert restored.seen(1)
            assert restored.memory_extraction_cursor(scope) == 2
        finally:
            restored.close()
    finally:
        store.close()
