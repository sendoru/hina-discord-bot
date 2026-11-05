"""Raw retention must never turn an extractor/summary delay into source loss."""

import sqlite3

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def ids(store, table, scope):
    return [row[0] for row in store.db.execute(
        f"SELECT message_id FROM {table} WHERE scope=? ORDER BY id", (scope.conversation,),
    )]


def test_over_twelve_pending_survives_restart_and_dedup_remains_effective(tmp_path, caplog):
    path = tmp_path / "retention.sqlite3"
    scope = Scope(1, 10, 100, True)
    store = Store.create(path, history_turns=12, raw_retention_turns=16)
    for number in range(1, 41):
        store.record_completed_turn(scope, number, "input", "reply")
    assert len(store.history(scope)) == 12
    assert store.db.execute("SELECT COUNT(*) FROM memory_extraction_cursors").fetchone()[0] == 0
    assert len(store.pending_memory_extraction(scope)) == 40
    assert len(store.pending_shared(scope)) == 40
    assert store.db.execute("SELECT COUNT(*) FROM memory_extraction_cursors").fetchone()[0] == 1
    assert "Protected conversation backlog" in caplog.text
    assert "guild:1" not in caplog.text
    store.close()

    store = Store.open_existing(path, history_turns=12, raw_retention_turns=16)
    try:
        assert store.seen(1)
        with pytest.raises(sqlite3.IntegrityError):
            store.record_completed_turn(scope, 1, "redelivery", "reply")
        assert len(store.pending_memory_extraction(scope)) == 40
        assert len(store.pending_shared(scope)) == 40
        assert [r["message_id"] for r in store.history(scope)] == list(map(str, range(29, 41)))
        assert store.chat_history_records(scope, [1])[0]["message_id"] == "1"
        assert len(store.public_context([scope])[0]["recent_user_messages"]) == 2
    finally:
        store.close()


def test_independent_id_spaces_cursors_and_scope_do_not_protect_wrong_rows(tmp_path):
    store = Store.create(tmp_path / "ids.sqlite3", history_turns=2, raw_retention_turns=3)
    scope, other = Scope(1, 10, 100, True), Scope(None, 11, 200)
    try:
        for number in range(1, 21):
            store.add(other, number, "private", "reply")
        for number in range(21, 31):
            store.record_completed_turn(scope, number, "public", "reply")
        store.save_memory_extraction_cursor(scope, 30)
        store.record_completed_turn(scope, 31, "public", "reply")
        assert ids(store, "turns", scope) == ["29", "30", "31"]
        assert ids(store, "shared_calls", scope) == list(map(str, range(21, 32)))
        assert len(ids(store, "turns", other)) == 20
        # shared_calls IDs are 1..11, not the turns IDs 21..31.
        previous, pending = store.shared_summary_snapshot(scope)
        assert len(pending) == 2
        store.commit_shared_summary(scope, pending, expected_summary=previous, text="summary")
        store.record_completed_turn(scope, 32, "public", "reply")
        assert ids(store, "shared_calls", scope) == list(map(str, range(23, 33)))
        assert store.memory_extraction_cursor(scope) == 30
        assert store.shared_summary(scope)[1] == 2
        assert not store.seen(21)
        assert store.chat_history_records(scope, [21]) == []
    finally:
        store.close()


def test_cleanup_is_bounded_and_does_not_advance_cursor_or_delete_memory(tmp_path):
    store = Store.create(tmp_path / "bounded.sqlite3", history_turns=2, raw_retention_turns=2)
    scope = Scope(None, 10, 100)
    try:
        # An outage may leave more than one cleanup batch eligible on recovery.
        for number in range(1, 1021):
            store.add(scope, number, "input", "reply")
        store.save_memory_extraction_cursor(scope, 1020)
        item_id = store.add_memory_item(scope, "keep", kind="fact", disclosure="local",
                                        source_message_ids=("1",))
        store.add(scope, 1021, "input", "reply")
        assert len(ids(store, "turns", scope)) == 21
        assert store.memory_extraction_cursor(scope) == 1020
        assert store.memory_items(100)[0].id == item_id
        store.add(scope, 1022, "input", "reply")
        assert ids(store, "turns", scope) == ["1021", "1022"]
        assert store.memory_extraction_cursor(scope) == 1020
        assert len(store.pending_memory_extraction(scope)) == 2
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["off", "read_only"])
def test_mode_change_protects_existing_pending_and_explicit_purge_still_wins(tmp_path, mode):
    store = Store.create(tmp_path / "mode.sqlite3", history_turns=2, raw_retention_turns=2)
    scope = Scope(1, 10, 100, True)
    try:
        for number in range(1, 16):
            store.record_completed_turn(scope, number, "input", "reply")
        store.set_user_memory_mode(100, mode)
        assert store.memory_mode(scope) == mode
        assert len(store.pending_memory_extraction(scope)) == 15
        assert len(store.pending_shared(scope)) == 15
        assert store.memory_extraction_cursor(scope) == 0
        store.set_user_memory_mode(100, "normal")
        assert len(store.pending_memory_extraction(scope)) == 15
        store.set_user_memory_mode(100, mode)
        store.forget(scope)
        assert store.pending_memory_extraction(scope) == []
        assert store.pending_shared(scope) == []
        assert not store.seen(1)
    finally:
        store.close()


def test_failure_audit_has_finite_independent_retention_and_never_becomes_input(tmp_path):
    store = Store.create(tmp_path / "failure.sqlite3", history_turns=2, raw_retention_turns=5)
    scope = Scope(None, 10, 100)
    try:
        for number in range(1, 21):
            store.add_failed_turn(scope, number, "failed", status="failed", stage="answer",
                                  reply_delivered=False)
        assert ids(store, "failed_turns", scope) == list(map(str, range(16, 21)))
        assert not store.seen(20)
        assert store.pending_memory_extraction(scope) == []
        assert store.pending_shared(scope) == []
    finally:
        store.close()


@pytest.mark.parametrize("value", ["0", "11", "100001"])
def test_retention_env_is_validated_without_expanding_prompt_history(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("HISTORY_TURNS", "12")
    monkeypatch.setenv("RAW_RETENTION_TURNS", value)
    with pytest.raises(ValueError, match="RAW_RETENTION_TURNS"):
        Settings.load()
    monkeypatch.setenv("RAW_RETENTION_TURNS", "1000")
    settings = Settings.load()
    assert settings.history_turns == 12
    assert settings.raw_retention_turns == 1000
