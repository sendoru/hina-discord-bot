from datetime import UTC, datetime, timedelta

from hina_bot.ai.reference_context_blocks import read_reference_context, reference_context_blocks
from hina_bot.ai.request_assembly import (
    RequestAssembler,
    _annotate_elapsed_minutes,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_dm_recent_history_preserves_timestamp_on_both_sides():
    scope = Scope(None, 10, 100)
    store = Store.create(":memory:")
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
            now=datetime.fromisoformat("2026-09-21T06:30:00+00:00"),
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


def test_server_exact_recent_tail_survives_cursor_advancement():
    scope = Scope(1, 10, 100, True)
    store = Store.create(":memory:")
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
        store.save_memory_extraction_cursor(scope, turns[-1]["id"])

        recent = RequestAssembler._server_recent_conversation(store, scope, [])

        assert [row["message_id"] for row in recent] == ["1", "2", "3", "4"]
        assert recent[-1]["user"] == "사용자 4"
        assert recent[-1]["hina"] == "히나 4"
        assert all(row["at"].endswith("Z") for row in recent)
    finally:
        store.close()


def test_server_exact_recent_tail_deduplicates_live_channel_rows():
    scope = Scope(1, 10, 100, True)
    store = Store.create(":memory:")
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


def test_old_nighttime_context_is_aged_against_current_morning_clock():
    now = datetime.fromisoformat("2026-10-16T07:22:00+09:00")
    old_at = "2026-10-15T15:35:00Z"  # Previous day 00:35 in Asia/Seoul.
    context = {
        "active_reply_chain": [{"at": old_at, "content": "밤새 일할 테니 자러 가"}],
        "channel_recent_messages": [{"at": old_at, "content": "잘 자"}],
        "personal_recent_conversation": [
            {"at": old_at, "user": "히나야 잘 자", "hina": "먼저 자러 가"}
        ],
        "conversation_history": [
            {"role": "assistant", "at": old_at, "content": "먼저 자러 가"}
        ],
    }

    projected = _annotate_elapsed_minutes(context, now=now)

    for section in context:
        assert projected[section][0]["elapsed_minutes"] == 407
        assert projected[section][0]["at"] == old_at
        assert "elapsed_minutes" not in context[section][0]

    # This metadata reaches the same reference blocks used for the model request.
    sent = read_reference_context(reference_context_blocks(projected))
    assert sent["active_reply_chain"][0]["elapsed_minutes"] == 407
    assert sent["personal_recent_conversation"][0]["elapsed_minutes"] == 407


def test_elapsed_age_respects_offsets_and_includes_recent_turns():
    now = datetime.fromisoformat("2026-10-16T07:22:00+09:00")
    context = {
        "active_reply_chain": [
            {"at": "2026-10-16T06:23:00+09:00", "content": "59분 전"},
            {"at": "2026-10-16T09:00:00+09:00", "content": "미래 날짜"},
            {"at": "bad-timestamp", "content": "날짜 오류"},
            {"content": "알 수 없는 시각"},
        ],
        "channel_recent_messages": [
            {"at": "2026-10-15T15:35:00", "content": "UTC naive timestamp"},
            {"at": "2026-10-15T15:35:00+00:00", "content": "UTC aware timestamp"},
            {"at": "2026-10-16T06:22:00+09:00", "content": "정확히 한 시간 전"},
        ],
    }

    projected = _annotate_elapsed_minutes(context, now=now)

    assert projected["active_reply_chain"][0]["elapsed_minutes"] == 59
    assert projected["active_reply_chain"][0]["at_local"] == "2026-10-16T06:23+09:00"
    assert "elapsed_minutes" not in projected["active_reply_chain"][1]
    assert "elapsed_minutes" not in projected["active_reply_chain"][2]
    assert "elapsed_minutes" not in projected["active_reply_chain"][3]
    assert [row["elapsed_minutes"] for row in projected["channel_recent_messages"]] == [
        407, 407, 60
    ]
    assert all("elapsed_minutes" not in row for row in context["channel_recent_messages"])


def _mark_ages(store, now, ages_by_message_id):
    for message_id, hours in ages_by_message_id.items():
        at = (now - timedelta(hours=hours)).isoformat()
        store.db.execute(
            "UPDATE turns SET created_at=? WHERE message_id=?",
            (at, str(message_id)),
        )
    store.db.commit()


def test_dm_auto_history_excludes_previous_session_without_deleting_records():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    scope = Scope(None, 10, 100)
    store = Store.create(":memory:")
    try:
        for message_id in range(1, 5):
            store.add(scope, message_id, f"user-{message_id}", f"hina-{message_id}")
        # 4h gap from previous (8h -> 3h), so only last two belong to active session.
        _mark_ages(store, now, {1: 9, 2: 8, 3: 3, 4: 1})
        history, ids = RequestAssembler._dm_conversation_history(
            store, scope, 12000, gap_minutes=240, now=now
        )
        assert ids == ["3", "4"]
        assert [r["content"] for r in history] == [
            "user-3", "hina-3", "user-4", "hina-4"
        ]
        assert len(store.history(scope)) == 4  # still persisted for later explicit lookup
    finally:
        store.close()


def test_server_personal_recent_does_not_backfill_old_session_when_deduplicated():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    scope = Scope(1, 10, 100)
    store = Store.create(":memory:")
    try:
        for message_id in range(1, 5):
            store.add(scope, message_id, f"user-{message_id}", f"hina-{message_id}")
        _mark_ages(store, now, {1: 8, 2: 7, 3: 2, 4: 1})
        selected = RequestAssembler._server_recent_conversation(
            store, scope, [{"message_id": "4"}], gap_minutes=240, now=now
        )
        assert [r["message_id"] for r in selected] == ["3"]
        assert len(store.history(scope)) == 4
    finally:
        store.close()


def test_server_auto_recent_keeps_four_turn_and_character_budgets_in_session():
    now = datetime.now(UTC)
    scope = Scope(1, 10, 100)
    store = Store.create(":memory:")
    try:
        for message_id in range(1, 7):
            store.add(scope, message_id, f"user-{message_id}", f"hina-{message_id}")
        rows = RequestAssembler._server_recent_conversation(
            store, scope, [], now=now + timedelta(seconds=3)
        )
        assert [row["message_id"] for row in rows] == ["3", "4", "5", "6"]
    finally:
        store.close()
