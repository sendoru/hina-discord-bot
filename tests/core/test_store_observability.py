import sqlite3

from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_store_migrates_turn_id_and_persists_current_trace(tmp_path):
    path = tmp_path / "legacy.sqlite3"
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
    db.commit()
    db.close()

    store = Store(str(path))
    token = CURRENT_TURN_ID.set("opaque-dashboard-trace")
    try:
        store.add(Scope(1, 10, 100, True), 123, "hello", "reply")
    finally:
        CURRENT_TURN_ID.reset(token)

    row = store.db.execute(
        "SELECT turn_id FROM turns WHERE message_id='123'"
    ).fetchone()
    columns = {item["name"] for item in store.db.execute("PRAGMA table_info(turns)")}
    indexes = {item["name"] for item in store.db.execute("PRAGMA index_list(turns)")}
    store.close()

    assert "turn_id" in columns
    assert "turns_turn_id" in indexes
    assert row["turn_id"] == "opaque-dashboard-trace"


def test_store_allows_non_runtime_rows_without_trace_id():
    store = Store(":memory:")
    try:
        store.add(Scope(None, 10, 100), 1, "hello", "reply")
        row = store.history(Scope(None, 10, 100))[0]
        assert row["turn_id"] is None
    finally:
        store.close()
