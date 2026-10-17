"""Completed turns and their eligible public projection share one commit."""

import json
import sqlite3

import pytest

from hina_bot.core.db_schema import inspect_connection
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE, CURRENT_MEMORY_CONTEXT
from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "turns.sqlite3"
    first = Store.create(path, history_turns=2)
    second = Store.open_existing(path, history_turns=2)
    scope = Scope(1, 10, 100, True)
    for message_id in (101, 102):
        first.add(scope, message_id, "source", "reply", name="name")
        first.add_shared_call(scope, message_id, "name", "source")
    try:
        yield first, second, scope
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("table,action", [
    ("turns", "INSERT"), ("turns", "DELETE"),
    ("shared_calls", "INSERT"), ("shared_calls", "DELETE"),
])
def test_each_statement_failure_rolls_back_inserts_pruning_and_sequences(stores, table, action):
    first, second, scope = stores
    before = list(second.db.iterdump())
    first.db.execute(f"""CREATE TEMP TRIGGER reject_completed BEFORE {action} ON {table}
                         BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        first.record_completed_turn(scope, 103, "new", "reply", name="name")
    assert list(second.db.iterdump()) == before
    assert not first.db.in_transaction
    assert not second.seen(103)


def test_both_tables_become_visible_together_and_keep_the_existing_retention(stores):
    first, second, scope = stores
    observed = []

    def observe():
        observed.append(([row["message_id"] for row in second.history(scope)],
                         [row["message_id"] for row in second.pending_shared(scope)]))
        return 0

    first.db.create_function("observe_completed", 0, observe)
    first.db.execute("""CREATE TEMP TRIGGER observe_completed AFTER INSERT ON shared_calls
                        BEGIN SELECT observe_completed(); END""")
    first.record_completed_turn(scope, 103, "new", "reply", name="name")
    assert observed == [(["101", "102"], ["101", "102"])]
    assert [row["message_id"] for row in second.history(scope)] == ["102", "103"]
    assert [row["message_id"] for row in second.pending_shared(scope)] == ["102", "103"]
    assert inspect_connection(first.db, ("store", "settings")) == ()


@pytest.mark.parametrize("guild_id,public", [(None, False), (1, False), (1, True)])
def test_scope_and_provenance_contracts_are_preserved(tmp_path, guild_id, public):
    store = Store.create(tmp_path / "scope.sqlite3")
    scope = Scope(guild_id, 10, 100, public)
    context = ({"message_id": "99", "content": "causal context"},)
    provenance = {"source_message_ids": ["98", "99"]}
    context_token = CURRENT_MEMORY_CONTEXT.set(context)
    provenance_token = CURRENT_CONTEXT_PROVENANCE.set(provenance)
    turn_token = CURRENT_TURN_ID.set("trace-test")
    try:
        store.record_completed_turn(scope, 101, "input", "reply", name="name")
        row = store.history(scope)[0]
        assert json.loads(row["memory_context"]) == list(context)
        assert json.loads(row["context_provenance"]) == provenance
        assert row["turn_id"] == "trace-test"
        assert row["exportable"] == int(public)
        assert row["name"] == "name"
        assert len(store.pending_shared(scope)) == int(guild_id is not None and public)
        assert CURRENT_MEMORY_CONTEXT.get() == ()
        assert CURRENT_CONTEXT_PROVENANCE.get() is None
    finally:
        CURRENT_MEMORY_CONTEXT.reset(context_token)
        CURRENT_CONTEXT_PROVENANCE.reset(provenance_token)
        CURRENT_TURN_ID.reset(turn_token)
        store.close()


def test_duplicate_input_never_commits_a_partial_projection(stores):
    first, second, scope = stores
    before = list(second.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):
        first.record_completed_turn(scope, 101, "duplicate", "reply", name="name")
    assert list(second.db.iterdump()) == before


def test_existing_transaction_is_untouched(stores):
    first, second, scope = stores
    first.db.execute("BEGIN IMMEDIATE")
    first.db.execute("INSERT INTO notes VALUES ('uncommitted','keep')")
    try:
        with pytest.raises(RuntimeError, match="transaction"):
            first.record_completed_turn(scope, 103, "new", "reply", name="name")
        assert first.db.in_transaction
        assert first.note("uncommitted") == "keep"
        assert second.note("uncommitted") == ""
    finally:
        first.db.rollback()


def test_commit_failure_restores_both_original_histories_and_sequences(tmp_path):
    class Connection(sqlite3.Connection):
        fail_commit = False

        def commit(self):
            if self.fail_commit:
                raise sqlite3.OperationalError("commit failed")
            super().commit()

    path = tmp_path / "commit.sqlite3"
    Store.create(path).close()
    db = sqlite3.connect(path, factory=Connection)
    db.row_factory = sqlite3.Row
    first, second = Store(db, history_turns=1), Store.open_existing(path)
    scope = Scope(1, 10, 100, True)
    try:
        first.record_completed_turn(scope, 101, "old", "reply", name="name")
        before = list(second.db.iterdump())
        db.fail_commit = True
        with pytest.raises(sqlite3.OperationalError, match="commit failed"):
            first.record_completed_turn(scope, 102, "new", "reply", name="name")
        assert list(second.db.iterdump()) == before
        assert not first.db.in_transaction
    finally:
        first.close()
        second.close()
