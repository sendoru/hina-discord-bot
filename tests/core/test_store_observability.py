
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_store_allows_non_runtime_rows_without_trace_id():
    store = Store.create(":memory:")
    try:
        store.add(Scope(None, 10, 100), 1, "hello", "reply")
        row = store.history(Scope(None, 10, 100))[0]
        assert row["turn_id"] is None
    finally:
        store.close()
