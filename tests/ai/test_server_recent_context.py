from hina_bot.ai.information_pipeline import LLM
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_uses_exact_recent_turns_not_already_in_channel_context():
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    try:
        store.add(scope, 1, "one", "reply one")
        store.add(scope, 2, "two", "reply two")
        store.add(scope, 3, "three", "reply three")
        rows = LLM._server_recent_conversation(
            store,
            scope,
            [{"message_id": 3, "content": "three"}],
        )
        assert [row["user"] for row in rows] == ["one", "two"]
    finally:
        store.close()


def test_saved_summary_does_not_remove_exact_recent_tail():
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    try:
        store.add(scope, 11, "before", "before reply")
        through = store.history(scope)[-1]["id"]
        store.save_summary(scope, "saved", through)
        store.add(scope, 12, "after", "after reply")
        rows = LLM._server_recent_conversation(store, scope, [])
        assert [row["user"] for row in rows] == ["before", "after"]
        assert [row["hina"] for row in rows] == ["before reply", "after reply"]
    finally:
        store.close()


def test_caps_server_recent_context_at_four_turns():
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    try:
        for i in range(1, 7):
            store.add(scope, 100 + i, f"q{i}", f"a{i}")
        rows = LLM._server_recent_conversation(store, scope, [])
        assert [row["user"] for row in rows] == ["q3", "q4", "q5", "q6"]
    finally:
        store.close()


def test_dm_does_not_use_server_recent_context():
    store = Store(":memory:")
    scope = Scope(None, 20, 100)
    try:
        store.add(scope, 21, "dm", "dm reply")
        assert LLM._server_recent_conversation(store, scope, []) == []
    finally:
        store.close()
