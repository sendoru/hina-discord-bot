from hina_bot.ai.request_assembly import RequestAssembler
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_dm_recent_history_preserves_timestamp_on_both_sides():
    scope = Scope(None, 10, 100)
    store = Store(":memory:")
    try:
        store.add(scope, 1, "애옹", "왜 갑자기 고양이 흉내야?", name="A")
        store.db.execute(
            "UPDATE turns SET created_at=? WHERE message_id=?",
            ("2026-09-21 06:20:33", "1"),
        )
        store.db.commit()

        history, message_ids = RequestAssembler._dm_conversation_history(
            store,
            scope,
            12000,
        )

        assert message_ids == ["1"]
        assert history == [
            {
                "role": "user",
                "at": "2026-09-21T06:20:33Z",
                "content": "애옹",
            },
            {
                "role": "assistant",
                "at": "2026-09-21T06:20:33Z",
                "content": "왜 갑자기 고양이 흉내야?",
            },
        ]
    finally:
        store.close()


def test_server_exact_recent_tail_survives_summary_advancement():
    scope = Scope(1, 10, 100, True)
    store = Store(":memory:")
    try:
        for message_id in range(1, 5):
            store.add(
                scope,
                message_id,
                f"사용자 {message_id}",
                f"히나 {message_id}",
                name="A",
            )
        turns = store.history(scope)
        store.save_summary(scope, "이미 이 턴들까지 요약됨", turns[-1]["id"])

        recent = RequestAssembler._server_recent_conversation(store, scope, [])

        assert [row["message_id"] for row in recent] == ["1", "2", "3", "4"]
        assert recent[-1]["user"] == "사용자 4"
        assert recent[-1]["hina"] == "히나 4"
        assert all(row["at"].endswith("Z") for row in recent)
    finally:
        store.close()


def test_server_exact_recent_tail_deduplicates_live_channel_rows():
    scope = Scope(1, 10, 100, True)
    store = Store(":memory:")
    try:
        store.add(scope, 1, "첫 질문", "첫 답변", name="A")
        store.add(scope, 2, "둘째 질문", "둘째 답변", name="A")

        recent = RequestAssembler._server_recent_conversation(
            store,
            scope,
            [{"message_id": "2", "content": "둘째 질문"}],
        )

        assert [row["message_id"] for row in recent] == ["1"]
    finally:
        store.close()


def test_channel_recent_timestamp_is_normalized_and_internal_clock_removed():
    rows = RequestAssembler._bind_current_speaker(
        [{
            "message_id": "1",
            "user_id": "100",
            "author_user_id": "100",
            "role": "user",
            "content": "방금 한 말",
            "unix_time": 1789999999.0,
            "received_at": 123.45,
        }],
        100,
    )

    assert rows[0]["at"].endswith("+00:00")
    assert "unix_time" not in rows[0]
    assert "received_at" not in rows[0]
    assert rows[0]["is_current_speaker"] is True
