import json
import sqlite3

from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_store_migrates_turns_with_context_provenance_column(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            realm TEXT NOT NULL,
            user_id TEXT NOT NULL,
            message_id TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,
            reply TEXT NOT NULL,
            exportable INTEGER NOT NULL,
            memory_context TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.commit()
    db.close()

    store = Store(str(path))
    try:
        columns = {
            row["name"]
            for row in store.db.execute("PRAGMA table_info(turns)")
        }
        assert "context_provenance" in columns
    finally:
        store.close()


def test_store_consumes_current_context_provenance_once():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    snapshot = {
        "version": 1,
        "sections": [{"name": "channel_recent_messages", "count": 1}],
    }
    CURRENT_CONTEXT_PROVENANCE.set(snapshot)
    try:
        store.add(scope, 1, "first", "reply")
        first = store.history(scope)[-1]
        assert json.loads(first["context_provenance"]) == snapshot
        assert CURRENT_CONTEXT_PROVENANCE.get() is None

        store.add(scope, 2, "second", "reply")
        second = store.history(scope)[-1]
        assert second["context_provenance"] == ""
    finally:
        CURRENT_CONTEXT_PROVENANCE.set(None)
        store.close()
