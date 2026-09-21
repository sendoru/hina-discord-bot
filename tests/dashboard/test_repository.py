import sqlite3

import pytest

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.repository import AdminRepository


def populated_database(path):
    store = Store(str(path))
    scope = Scope(1, 10, 100, True)
    token = CURRENT_TURN_ID.set("trace-123")
    try:
        store.add(scope, 55, "question", "answer")
    finally:
        CURRENT_TURN_ID.reset(token)
    store.add_memory_item(
        scope,
        "likes coffee",
        kind="preference",
        disclosure="implicit",
        source_message_ids=("55",),
        confidence=0.9,
    )
    store.close()


def test_repository_reads_turns_and_memory_by_trace(tmp_path):
    path = tmp_path / "hina.sqlite3"
    populated_database(path)
    repository = AdminRepository(path)

    turns = repository.recent_turns()
    assert len(turns) == 1
    assert turns[0]["turn_id"] == "trace-123"
    assert turns[0]["content"] == "question"
    assert repository.turn_for_trace("trace-123")["reply"] == "answer"

    memories = repository.recent_memory_items()
    assert len(memories) == 1
    assert memories[0]["content"] == "likes coffee"


def test_repository_connection_is_sqlite_read_only(tmp_path):
    path = tmp_path / "hina.sqlite3"
    populated_database(path)
    repository = AdminRepository(path)

    with repository._connection() as db, pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM turns")

    writable = sqlite3.connect(path)
    try:
        assert writable.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    finally:
        writable.close()


def test_repository_handles_pre_turn_id_database_without_migrating_it(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE turns (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               scope TEXT NOT NULL, realm TEXT NOT NULL, user_id TEXT NOT NULL,
               message_id TEXT NOT NULL UNIQUE,
               content TEXT NOT NULL, reply TEXT NOT NULL, exportable INTEGER NOT NULL,
               memory_context TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    db.execute(
        """INSERT INTO turns(scope,realm,user_id,message_id,content,reply,exportable)
           VALUES ('dm:1','dm:1','1','1','old question','old answer',0)"""
    )
    db.commit()
    db.close()

    repository = AdminRepository(path)
    row = repository.recent_turns()[0]
    assert row["turn_id"] is None
    assert repository.turn_for_trace("anything") is None

    verify = sqlite3.connect(path)
    try:
        columns = {row[1] for row in verify.execute("PRAGMA table_info(turns)")}
    finally:
        verify.close()
    assert "turn_id" not in columns
