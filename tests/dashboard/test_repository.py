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


def test_repository_search_turns_filters_without_writes(tmp_path):
    path = tmp_path / "hina.sqlite3"
    populated_database(path)
    repository = AdminRepository(path)

    assert repository.count_turns(query="question") == 1
    assert repository.count_turns(query="missing") == 0

    rows = repository.search_turns(user_id="100", query="answer", limit=10)
    assert len(rows) == 1
    assert rows[0]["message_id"] == "55"


def test_repository_memory_inspection_and_future_lifecycle_columns(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store(str(path))
    scope = Scope(1, 10, 100, True)
    token = CURRENT_TURN_ID.set("memory-source-trace")
    try:
        store.add(scope, 700, "source message", "reply")
    finally:
        CURRENT_TURN_ID.reset(token)
    first_id = store.add_memory_item(
        scope,
        "first memory",
        kind="fact",
        disclosure="reference_gated",
        source_message_ids=("700",),
        confidence=0.8,
    )
    store.add_memory_item(
        scope,
        "second memory",
        kind="preference",
        disclosure="implicit",
        confidence=0.9,
    )
    store.close()

    repository = AdminRepository(path)
    assert repository.memory_schema()["has_lifecycle"] is False
    assert repository.count_memory_items(user_id="100") == 2
    assert repository.count_memory_items(query="first") == 1
    assert repository.count_memory_items(relationship="yes") == 0

    item = repository.memory_item(first_id)
    assert item is not None
    assert item["content"] == "first memory"
    assert len(repository.neighboring_memory_items(item)) == 1
    sources = repository.turns_for_message_ids(["700", "missing"])
    assert len(sources) == 1
    assert sources[0]["turn_id"] == "memory-source-trace"

    writable = sqlite3.connect(path)
    writable.execute("ALTER TABLE memory_items ADD COLUMN status TEXT")
    writable.execute("ALTER TABLE memory_items ADD COLUMN superseded_by INTEGER")
    writable.execute("UPDATE memory_items SET status='active'")
    writable.commit()
    writable.close()

    repository = AdminRepository(path)
    schema = repository.memory_schema()
    assert schema["has_lifecycle"] is True
    assert schema["has_superseded_by"] is True
    assert repository.count_memory_items(status="active") == 2
    assert repository.count_memory_items(status="superseded") == 0


def test_repository_summary_and_extraction_cursor_status(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store(str(path))
    scope = Scope(None, 10, 100)
    store.add(scope, 1, "first", "reply")
    first_id = int(store.db.execute(
        "SELECT id FROM turns WHERE message_id='1'"
    ).fetchone()["id"])
    store.save_summary(scope, "legacy summary", first_id)
    store.add(scope, 2, "second", "reply")
    store.save_memory_extraction_cursor(scope, first_id)

    other = Scope(None, 20, 200)
    store.add(other, 3, "uninitialized", "reply")
    store.save_summary(other, "baseline summary", 0)

    with store.db:
        store.db.execute(
            """INSERT INTO shared_calls(scope,realm,user_id,message_id,name,content)
               VALUES (?,?,?,?,?,?)""",
            (scope.conversation, scope.realm, "100", "900", "User", "shared one"),
        )
        shared_first = int(store.db.execute(
            "SELECT id FROM shared_calls WHERE message_id='900'"
        ).fetchone()["id"])
        store.db.execute(
            """INSERT INTO shared_calls(scope,realm,user_id,message_id,name,content)
               VALUES (?,?,?,?,?,?)""",
            (scope.conversation, scope.realm, "100", "901", "User", "shared two"),
        )
        store.db.execute(
            """INSERT INTO shared_summaries(scope,realm,user_id,name,text,through_id)
               VALUES (?,?,?,?,?,?)""",
            (scope.conversation, scope.realm, "100", "User", "shared summary", shared_first),
        )
    store.close()

    repository = AdminRepository(path)
    personal = {row["scope"]: row for row in repository.personal_summary_status()}
    assert personal[scope.conversation]["pending_turns"] == 1
    assert personal[other.conversation]["pending_turns"] == 1

    shared = repository.shared_summary_status()
    assert len(shared) == 1
    assert shared[0]["pending_calls"] == 1

    cursors = {row["scope"]: row for row in repository.extraction_cursor_status()}
    assert cursors[scope.conversation]["initialized"] == 1
    assert cursors[scope.conversation]["pending_turns"] == 1
    assert cursors[other.conversation]["initialized"] == 0
    assert cursors[other.conversation]["effective_through_id"] == 0
    assert cursors[other.conversation]["pending_turns"] == 1
