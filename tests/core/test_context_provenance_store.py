import json

from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_store_consumes_current_context_provenance_once():
    store = Store.create(":memory:")
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
