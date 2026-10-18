"""Behavior contracts for the Store split, including intentionally bounded retention.

These describe current behavior, not the future archive/reset policy. Keep policy
changes separate from moving SQL between modules.
"""

import sqlite3

import pytest

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store.create(str(tmp_path / "contracts.sqlite3"), history_turns=2)
    try:
        yield instance
    finally:
        instance.close()


def test_raw_retention_is_per_scope_and_can_remove_unextracted_sources(store):
    scope = Scope(1, 10, 100, True)
    other = Scope(1, 10, 200, True)
    store.add(other, 90, "other", "reply")
    for message_id in (101, 102, 103):
        store.add(scope, message_id, str(message_id), "reply")

    assert [row["message_id"] for row in store.history(scope)] == ["102", "103"]
    assert [row["message_id"] for row in store.history(other)] == ["90"]
    assert store.memory_extraction_cursor(scope) == 0
    assert [row["message_id"] for row in store.pending_memory_extraction(scope)] == [
        "102", "103",
    ]
    assert not store.seen(101)
    assert store.seen(102)


def test_pruning_failure_rolls_back_new_turn_and_keeps_previous_history(store):
    scope = Scope(None, 10, 100)
    for message_id in (101, 102):
        store.add(scope, message_id, str(message_id), "reply")
    before = [dict(row) for row in store.history(scope)]
    store.db.execute(
        """CREATE TRIGGER reject_turn_prune BEFORE DELETE ON turns
           BEGIN SELECT RAISE(ABORT, 'injected prune failure'); END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected prune failure"):
        store.add(scope, 103, "new", "reply")

    assert [dict(row) for row in store.history(scope)] == before
    assert not store.seen(103)
    assert not store.db.in_transaction


def test_shared_cursor_uses_shared_ids_and_failure_rows_are_not_memory_input(store):
    scope = Scope(1, 10, 100, True)
    for message_id in (101, 102, 103):
        store.add(scope, message_id, str(message_id), "reply")
    with store.db:
        store._conversations.write_shared_call(scope, 103, "name", "public")
    shared = store.pending_shared(scope)
    assert len(shared) == 1
    assert shared[0]["id"] != store.history(scope)[-1]["id"]
    with store.db:
        store._conversations.write_shared_summary(scope, "name", "summary", shared[0]["id"])
    assert store.pending_shared(scope) == []

    for message_id in (201, 202, 203):
        store.add_failed_turn(
            scope, message_id, "failed input", status="failed",
            stage="generation", reply_delivered=False,
        )
    assert [row[0] for row in store.db.execute(
        "SELECT message_id FROM failed_turns ORDER BY id"
    )] == ["202", "203"]
    assert [row["message_id"] for row in store.pending_memory_extraction(scope)] == [
        "102", "103",
    ]
    assert store.shared_summary(scope) == ("summary", shared[0]["id"])


def test_atomic_memory_batch_is_invisible_until_commit_and_rolls_back(store):
    scope = Scope(None, 10, 100)
    store.add(scope, 101, "a preference", "reply")
    pending = store.pending_memory_extraction(scope)
    database_path = store.db.execute("PRAGMA database_list").fetchone()[2]
    observer = sqlite3.connect(database_path)
    try:
        with (
            pytest.raises(RuntimeError, match="abort batch"),
            store.atomic_memory_batch(scope, pending, expected_cursor=0),
        ):
            store.add_memory_item(
                scope, "preference", kind="preference", disclosure="local",
                source_message_ids=("101",),
            )
            assert observer.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 0
            raise RuntimeError("abort batch")
        assert store.memory_items(100) == []
        assert store.memory_extraction_cursor(scope) == 0
        assert store.db.execute("SELECT COUNT(*) FROM memory_extraction_batches").fetchone()[0] == 0

        with store.atomic_memory_batch(scope, pending, expected_cursor=0):
            store.add_memory_item(
                scope, "preference", kind="preference", disclosure="local",
                source_message_ids=("101",),
            )
        assert observer.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 1
        assert store.memory_extraction_cursor(scope) == pending[-1]["id"]
        assert store.db.execute("SELECT COUNT(*) FROM memory_extraction_batches").fetchone()[0] == 1
    finally:
        observer.close()


@pytest.mark.parametrize("operation", [
    "forget", "purge_channel_memory", "purge_realm_memory", "purge_all_memory",
])
def test_current_purge_contract_preserves_failure_and_batch_records(store, operation):
    scope = Scope(1, 10, 100, True)
    store.add(scope, 101, "input", "reply")
    with store.db:
        store._conversations.write_shared_call(scope, 101, "name", "input")
    with store.db:
        store._conversations.write_shared_summary(scope, "name", "summary", 1)
    pending = store.pending_memory_extraction(scope)
    with store.atomic_memory_batch(scope, pending, expected_cursor=0):
        item_id = store.add_memory_item(
            scope, "fact", kind="fact", disclosure="local", source_message_ids=("101",),
        )
    store.revise_memory_item(
        item_id, expected_revision=0, content="edited", kind="fact",
        disclosure="local", confidence=1,
    )
    store.add_failed_turn(
        scope, 102, "failure", status="failed", stage="generation", reply_delivered=False,
    )
    store.set_note(scope.user_note, "manual")
    store.set_memory_mode_override(scope.channel, "read_only")

    if operation == "purge_all_memory":
        store.purge_all_memory()
    else:
        getattr(store, operation)(scope)

    for table in (
        "turns", "shared_calls", "shared_summaries", "memory_items",
        "memory_item_edit_history", "memory_extraction_cursors",
    ):
        assert store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    # Characterize the existing retention contract; do not silently change it in a refactor.
    assert store.db.execute("SELECT COUNT(*) FROM failed_turns").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM memory_extraction_batches").fetchone()[0] == 1
    assert store.note(scope.user_note) == "manual"
    assert store.memory_mode(scope) == "read_only"


def test_reopening_store_preserves_schema_rows_and_autoincrement_high_water(tmp_path):
    path = str(tmp_path / "reopen.sqlite3")
    scope = Scope(None, 10, 100)
    first = Store.create(path)
    try:
        first.add(scope, 101, "first", "reply")
        first.add(scope, 102, "second", "reply")
        first.save_memory_extraction_cursor(scope, first.history(scope)[-1]["id"])
        with first.db:
            first.db.execute("DELETE FROM turns")
        schema = [tuple(row) for row in first.db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        )]
    finally:
        first.close()

    second = Store.create(path)
    try:
        assert [tuple(row) for row in second.db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        )] == schema
        assert second.memory_extraction_cursor(scope) == 2
        second.add(scope, 103, "third", "reply")
        assert second.history(scope)[0]["id"] == 3
        assert second.pending_memory_extraction(scope)[0]["message_id"] == "103"
    finally:
        second.close()
