"""Focused regression tests for the four dialogue-continuity failures."""

from datetime import datetime

from hina_bot.ai.dialogue_context import project_dialogue_context
from hina_bot.ai.reference_context_blocks import read_reference_context, reference_context_blocks
from hina_bot.ai.request_assembly import _annotate_elapsed_minutes


def test_dialogue_lanes_preserve_all_messages_and_real_reply_targets():
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
    assert [r["message_id"] for r in projected["speaker_recent_messages"]] == ["3", "4"]
    assert [r["message_id"] for r in projected["channel_ambient_messages"]] == ["1", "2", "5", "6"]
    assert [r["message_id"] for r in projected["active_reply_chain"]] == ["0"]
    assert "channel_recent_messages" not in projected
    assert projected["conversation_focus"]["current_speaker_in_recent_channel"] is True
    assert projected["conversation_focus"]["explicit_reply_chain_present"] is True
    assert [r["message_id"] for r in context["channel_recent_messages"]] == [
        "1", "2", "3", "4", "5", "6"
    ]
    roundtrip = read_reference_context(reference_context_blocks(projected))
    assert roundtrip == projected


def test_new_speaker_not_treated_as_participant_of_prior_argument():
    projected = project_dialogue_context({
        "current_speaker": {"user_id": "C"},
        "active_reply_chain": [],
        "channel_recent_messages": [
            {"role": "user", "author_user_id": "A", "content": "argument"},
            {"role": "assistant", "reply_target_user_id": "A", "content": "response"},
        ],
    })
    assert projected["speaker_recent_messages"] == []
    assert len(projected["channel_ambient_messages"]) == 2
    assert projected["conversation_focus"]["current_speaker_in_recent_channel"] is False


def test_historical_morning_is_labeled_in_current_local_timezone():
    now = datetime.fromisoformat("2026-10-20T12:34:32+09:00")
    context = {
        "personal_recent_conversation": [
            {"at": "2026-10-19T23:35:11Z", "user": "졸린 아침", "hina": "좋은 아침"}
        ],
        "channel_recent_messages": [
            {"at": "2026-10-20T03:32:38+00:00", "content": "two minutes ago"}
        ],
    }
    projected = _annotate_elapsed_minutes(context, now=now)
    older = projected["personal_recent_conversation"][0]
    recent = projected["channel_recent_messages"][0]
    assert older["at_local"] == "2026-10-20T08:35+09:00"
    assert older["elapsed_minutes"] == 239
    assert recent["at_local"] == "2026-10-20T12:32+09:00"
    assert "elapsed_minutes" not in recent
    assert "at_local" not in context["personal_recent_conversation"][0]
