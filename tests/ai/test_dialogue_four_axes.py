"""Final model-input projections preserve historical and live dialogue evidence."""

from datetime import datetime

from hina_bot.ai.dialogue_context import project_dialogue_context
from hina_bot.ai.reference_context_blocks import read_reference_context, reference_context_blocks
from hina_bot.ai.request_assembly import _annotate_elapsed_minutes


def test_interleaved_dialogue_preserves_sequence_and_known_addressees():
    context = {
        "current_speaker": {"user_id": "B"},
        "active_reply_chain": [{"message_id": "0", "content": "explicit reply"}],
        "channel_recent_messages": [
            {"message_id": "1", "role": "user", "author_user_id": "A", "content": "A taunts"},
            {"message_id": "2", "role": "assistant", "author_user_id": "bot",
             "reply_target_user_id": "A", "content": "Hina responds to A"},
            {"message_id": "3", "role": "user", "author_user_id": "B", "content": "B joins"},
            {"message_id": "4", "role": "assistant", "author_user_id": "bot",
             "reply_target_user_id": "B", "content": "Hina responds to B"},
            {"message_id": "5", "role": "bot", "author_user_id": "other-bot", "content": "bot chatter"},
            {"message_id": "6", "role": "assistant", "author_user_id": "bot",
             "reply_target_user_id": None, "content": "unknown addressee"},
        ],
    }
    projected = project_dialogue_context(context)
    rows = projected["channel_recent_messages"]
    assert [r["message_id"] for r in rows] == ["1", "2", "3", "4", "5", "6"]
    assert [r["relation_to_current_speaker"] for r in rows] == [
        "other_author", "hina_reply_to_other", "current_author",
        "hina_reply_to_current", "other_bot", "unknown_addressee",
    ]
    assert projected["active_reply_chain"] == context["active_reply_chain"]
    assert "speaker_recent_messages" not in projected
    assert "channel_ambient_messages" not in projected
    assert "relation_to_current_speaker" not in context["channel_recent_messages"][0]
    assert read_reference_context(reference_context_blocks(projected)) == projected


def test_new_speaker_not_assigned_prior_arguments_and_unknowns_not_guessed():
    projected = project_dialogue_context({
        "current_speaker": {"user_id": "C"},
        "active_reply_chain": [],
        "channel_recent_messages": [
            {"role": "user", "author_user_id": "A", "content": "argument"},
            {"role": "assistant", "reply_target_user_id": "A", "content": "response"},
            {"role": "assistant", "content": "unknown target"},
            {"role": "user", "content": "unknown sender"},
        ],
    })
    assert [r["relation_to_current_speaker"] for r in projected["channel_recent_messages"]] == [
        "other_author", "hina_reply_to_other", "unknown_addressee", "unknown_author",
    ]


def test_stored_server_turn_preserves_verbatim_text_and_marks_past_reply_target():
    context = {
        "current_speaker": {"user_id": "101"},
        "personal_recent_conversation": [{
            "message_id": "42", "at": "2026-10-20T00:11:00Z",
            "at_local": "2026-10-20T09:11+09:00", "elapsed_minutes": 197,
            "user": "히나야 으헤", "hina": "……하아?\n왜?",
        }],
    }
    result = project_dialogue_context(context)["personal_recent_conversation"][0]
    assert result["context_kind"] == "historical_exchange"
    assert result["message_id"] == "42"
    assert result["elapsed_minutes"] == 197
    assert result["user_message"] == {
        "role": "user", "author_user_id": "101", "content": "히나야 으헤",
    }
    assert result["assistant_reply"] == {
        "role": "assistant", "reply_target_user_id": "101",
        "content": "……하아?\n왜?",
    }
    assert context["personal_recent_conversation"][0]["user"] == "히나야 으헤"


def test_dm_adjacent_turns_bundle_without_inventing_answer_send_times():
    context = {
        "current_speaker": {"user_id": "100"},
        "conversation_history": [
            {"role": "user", "at": "2026-10-20T03:00:00Z",
             "at_local": "2026-10-20T12:00+09:00", "elapsed_minutes": 10,
             "content": "첫 질문"},
            {"role": "assistant", "at": "2026-10-20T03:00:00Z",
             "at_local": "2026-10-20T12:00+09:00", "elapsed_minutes": 10,
             "content": "첫 답변"},
        ],
    }
    out = project_dialogue_context(context)["conversation_history"]
    assert len(out) == 1
    assert out[0]["at_local"] == "2026-10-20T12:00+09:00"
    assert out[0]["elapsed_minutes"] == 10
    assert out[0]["user_message"]["content"] == "첫 질문"
    assert out[0]["assistant_reply"]["content"] == "첫 답변"
    assert out[0]["assistant_reply"]["reply_target_user_id"] == "100"
    assert "replied_at" not in out[0]
    assert len(context["conversation_history"]) == 2


def test_unpaired_dm_messages_are_kept_without_fabricated_exchange():
    rows = [
        {"role": "assistant", "content": "orphan", "at": "2026-10-20T03:00:00Z"},
        {"role": "user", "content": "question", "at": "2026-10-20T03:01:00Z"},
        {"role": "assistant", "content": "not same turn", "at": "2026-10-20T03:02:00Z"},
    ]
    out = project_dialogue_context({
        "current_speaker": {"user_id": "100"},
        "conversation_history": rows,
    })["conversation_history"]
    assert len(out) == 3
    assert all(row["context_kind"] == "historical_message" for row in out)
    assert [row["content"] for row in out] == ["orphan", "question", "not same turn"]


def test_recent_and_older_historical_times_use_same_trusted_timezone():
    now = datetime.fromisoformat("2026-10-20T12:34:32+09:00")
    context = {
        "personal_recent_conversation": [{
            "at": "2026-10-19T23:35:11Z",
            "user": "졸린 아침", "hina": "좋은 아침",
        }],
        "channel_recent_messages": [{
            "at": "2026-10-20T03:32:38+00:00", "content": "two minutes ago",
        }],
    }
    projected = _annotate_elapsed_minutes(context, now=now)
    older = projected["personal_recent_conversation"][0]
    recent = projected["channel_recent_messages"][0]
    assert older["at_local"] == "2026-10-20T08:35+09:00"
    assert older["elapsed_minutes"] == 239
    assert recent["at_local"] == "2026-10-20T12:32+09:00"
    assert recent["elapsed_minutes"] == 1
    assert "at_local" not in context["personal_recent_conversation"][0]
