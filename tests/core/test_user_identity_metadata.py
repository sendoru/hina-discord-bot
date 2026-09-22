from hina_bot.core.memory_context import build_memory_context
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_persistent_user_metadata_keeps_readable_name():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    try:
        store.add(scope, 1, "hello", "hi", name="Sendol")
        turn = store.history(scope)[0]
        assert turn["user_id"] == "100"
        assert turn["name"] == "Sendol"

        store.save_summary(scope, "summary", turn["id"])
        summary = store.db.execute(
            "SELECT user_id,name,text FROM summaries WHERE scope=?",
            (scope.conversation,),
        ).fetchone()
        assert summary["user_id"] == "100"
        assert summary["name"] == "Sendol"
        assert summary["text"] == "summary"

        item_id = store.add_memory_item(
            scope,
            "사용자는 커피를 좋아한다.",
            kind=MemoryKind.PREFERENCE,
            disclosure=MemoryDisclosure.LOCAL,
            source_message_ids=("1",),
        )
        item = next(item for item in store.memory_items(scope.user_id) if item.id == item_id)
        assert item.user_id == "100"
        assert item.user_name == "Sendol"
    finally:
        store.close()


def test_memory_context_keeps_readable_author_name():
    context = build_memory_context(
        [
            {
                "context_kind": "replied_message",
                "role": "user",
                "author_user_id": "200",
                "name": "Alice",
                "content": "hello",
            }
        ],
        100,
    )

    assert context == [
        {
            "kind": "replied_message",
            "role": "user",
            "ownership": "external",
            "content": "hello",
            "author_user_id": "200",
            "author_name": "Alice",
        }
    ]
