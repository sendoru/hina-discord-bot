import sqlite3

from hina_bot.core.memory_context import (
    CURRENT_MEMORY_CONTEXT,
    build_memory_context,
    decode_memory_context,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_memory_context_keeps_only_strong_causal_rows_with_bounded_ownership():
    rows = [
        {
            "context_kind": "channel_ambient",
            "role": "user",
            "author_user_id": "300",
            "content": "unrelated",
        },
        {
            "context_kind": "reply_origin_source",
            "role": "user",
            "author_user_id": "200",
            "content": "나는 매운 음식을 못 먹어",
        },
        {
            "context_kind": "reply_origin_request",
            "role": "user",
            "author_user_id": "100",
            "content": "이 사람 말이랑 비슷한 사례가 있어?",
        },
        {
            "context_kind": "replied_message",
            "role": "assistant",
            "author_user_id": "999",
            "content": "이런 취향을 말하는 거야.",
        },
    ]

    context = build_memory_context(rows, 100)

    assert context == [
        {
            "kind": "replied_message",
            "role": "assistant",
            "ownership": "assistant",
            "content": "이런 취향을 말하는 거야.",
            "author_user_id": "999",
        },
        {
            "kind": "reply_origin_request",
            "role": "user",
            "ownership": "self",
            "content": "이 사람 말이랑 비슷한 사례가 있어?",
            "author_user_id": "100",
        },
        {
            "kind": "reply_origin_source",
            "role": "user",
            "ownership": "external",
            "content": "나는 매운 음식을 못 먹어",
            "author_user_id": "200",
        },
    ]
    assert sum(len(item["content"]) for item in context) <= 1600


def test_store_consumes_request_scoped_memory_context_once():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    context = ({
        "kind": "replied_message",
        "role": "user",
        "ownership": "external",
        "content": "난 이런 옷 좋아해",
        "author_user_id": "200",
        "provenance_class": "reference_material",
    },)
    token = CURRENT_MEMORY_CONTEXT.set(context)
    try:
        store.add(scope, 1, "히나야 나도 이거 좋아해", "그렇구나")
        store.add(scope, 2, "히나야 그리고 이것도", "응")
        rows = store.pending_memory_extraction(scope)
    finally:
        CURRENT_MEMORY_CONTEXT.reset(token)
        store.close()

    assert decode_memory_context(rows[0]["memory_context"]) == list(context)
    assert rows[1]["memory_context"] == ""


def test_store_migrates_existing_turns_table_with_memory_context(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    try:
        db.execute(
            "CREATE TABLE turns ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, realm TEXT NOT NULL, "
            "user_id TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE, content TEXT NOT NULL, "
            "reply TEXT NOT NULL, exportable INTEGER NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        db.commit()
    finally:
        db.close()

    store = Store(str(path))
    try:
        columns = {row["name"] for row in store.db.execute("PRAGMA table_info(turns)")}
        assert "memory_context" in columns
    finally:
        store.close()


def test_memory_context_prioritizes_inherited_reference_source_over_intermediate_assistant_source():
    rows = [
        {
            "context_kind": "reply_origin_source",
            "role": "assistant",
            "author_user_id": "999",
            "content": "미카가 킹콩이 된다니...",
            "source_turn_message_id": "5",
            "provenance_class": "conversation",
        },
        {
            "context_kind": "reply_reference_source",
            "role": "user",
            "author_user_id": "200",
            "content": "미카가 킹콩으로 변해서...",
            "source_turn_message_id": "5",
            "provenance_class": "reference_material",
        },
        {
            "context_kind": "reply_origin_request",
            "role": "user",
            "author_user_id": "100",
            "content": "난 안 그랬어",
            "source_turn_message_id": "5",
            "provenance_class": "conversation",
        },
        {
            "context_kind": "replied_message",
            "role": "assistant",
            "author_user_id": "999",
            "content": "그럼 아까 그런 소릴 꺼낸 게 누구였더라?",
            "provenance_class": "conversation",
        },
    ]

    context = build_memory_context(rows, 100)

    assert [item["kind"] for item in context] == [
        "replied_message",
        "reply_origin_request",
        "reply_reference_source",
    ]
    reference = context[-1]
    assert reference["ownership"] == "external"
    assert reference["author_user_id"] == "200"
    assert reference["provenance_class"] == "reference_material"
    assert reference["content"] == "미카가 킹콩으로 변해서..."
