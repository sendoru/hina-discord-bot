from hina_bot.ai.egress_policy import (
    BOT_INTERACTIONS_ONLY,
    FULL,
    apply_context_policy,
    filter_channel_context,
)


def test_full_policy_preserves_context_rows():
    rows = [{"role": "user", "user_id": "200", "content": "ambient"}]
    assert filter_channel_context(rows, 100, FULL) == rows


def test_bot_interactions_only_keeps_shared_bot_conversation_and_fails_closed():
    rows = [
        {"message_id": "1", "role": "user", "user_id": "100", "author_user_id": "100",
         "direct_trigger": True, "content": "히나야 직접 질문"},
        {"message_id": "2", "role": "user", "user_id": "100", "author_user_id": "100",
         "direct_trigger": False, "content": "내가 옆에서 한 일반 대화"},
        {"message_id": "3", "role": "user", "user_id": "200", "author_user_id": "200",
         "direct_trigger": True, "content": "다른 사람이 히나에게 직접 한 말"},
        {"message_id": "4", "role": "assistant", "reply_target_user_id": "100",
         "content": "현재 사용자에게 한 히나 답변"},
        {"message_id": "5", "role": "assistant", "reply_target_user_id": "200",
         "content": "다른 사용자에게 한 히나 답변"},
        {"message_id": "6", "role": "user", "user_id": "200", "author_user_id": "200",
         "direct_trigger": True, "context_kind": "target_user_history",
         "content": "7일 target history"},
        {"message_id": "7", "role": "user", "user_id": "100", "author_user_id": "100",
         "context_kind": "replied_message",
         "content": "이번 호출에서 내가 직접 골라 답장한 내 메시지"},
        {"message_id": "8", "role": "user", "user_id": "200", "author_user_id": "200",
         "context_kind": "replied_message", "content": "제3자 답장 원문"},
        {"message_id": "9", "role": "user", "user_id": "100", "author_user_id": "100",
         "content": "provenance 없는 legacy row"},
        {"message_id": "10", "role": "bot", "user_id": "300", "author_user_id": "300",
         "content": "다른 봇의 말"},
    ]

    filtered = filter_channel_context(rows, 100, BOT_INTERACTIONS_ONLY)
    assert [row["message_id"] for row in filtered] == ["1", "3", "4", "5", "7"]


def test_bot_interactions_only_filters_complete_serialized_context():
    context = {
        "server_note": "서버 공통 메모",
        "user_note": "현재 사용자가 직접 저장한 메모",
        "conversation_memory": "현재 사용자의 자동 기억",
        "personal_recent_conversation": [{"user": "직접 호출", "hina": "답변"}],
        "public_server_context": [
            {"user_id": "100", "summary": "현재 사용자의 다른 채널 직접 호출 기억"},
            {"user_id": "200", "summary": "다른 사용자의 공개 기억"},
        ],
        "channel_recent_messages": [
            {"role": "user", "user_id": "100", "author_user_id": "100",
             "direct_trigger": True, "content": "내 직접 호출"},
            {"role": "user", "user_id": "200", "author_user_id": "200",
             "direct_trigger": True, "content": "다른 사용자 직접 호출"},
            {"role": "assistant", "reply_target_user_id": "200",
             "content": "다른 사용자에게 한 히나 답변"},
        ],
        "conversation_history": [{"role": "user", "content": "DM 직접 대화"}],
        "available_custom_emojis": [{"alias": ":hina:", "description": "public asset"}],
        "lore_reference": [{"reference": "canon.test", "summary": "public lore"}],
    }

    filtered = apply_context_policy(context, 100, BOT_INTERACTIONS_ONLY)

    assert filtered["server_note"] == ""
    assert filtered["user_note"] == "현재 사용자가 직접 저장한 메모"
    assert filtered["conversation_memory"] == "현재 사용자의 자동 기억"
    assert filtered["personal_recent_conversation"] == context["personal_recent_conversation"]
    assert [row["user_id"] for row in filtered["public_server_context"]] == ["100"]
    assert [row["content"] for row in filtered["channel_recent_messages"]] == [
        "내 직접 호출",
        "다른 사용자 직접 호출",
        "다른 사용자에게 한 히나 답변",
    ]
    assert filtered["conversation_history"] == context["conversation_history"]
    assert filtered["lore_reference"] == context["lore_reference"]
    assert context["server_note"] == "서버 공통 메모"  # input is not mutated
