"""Untrusted context must be split into parts, not simulated user turns."""
from __future__ import annotations

import json

from hina_bot.ai.providers import _gemini_input
from hina_bot.ai.reference_context_blocks import reference_context_blocks
from tests.reference_blocks import read_reference_context


def test_reference_parts_round_trip_all_sections_without_duplication():
    context = {
        "data_notice": "untrusted reference data",
        "current_speaker": {"user_id": "200", "name": "speaker"},
        "current_interaction": {"reply_target": {"user_id": "300"}},
        "space": "server",
        "server_note": "note",
        "structured_owner_memory": [{"id": "fact-1", "content": "important"}],
        "structured_relation_new_field": {"role": "reference-only"},
        "active_reply_chain": [{"message_id": "11", "content": "reply"}],
        "channel_recent_messages": [{"message_id": "12", "content": "recent"}],
        "personal_recent_conversation": [{"user": "old", "hina": "reply"}],
        "conversation_history": [{"role": "user", "content": "old text"}],
        "public_server_context": [{"source": "public", "summary": "recent"}],
        "lore_reference": [{"reference": "canon"}],
        "character_insights": [{"insight": "context"}],
        "reaction_guides": [{"guide": "soft"}],
        "available_custom_emojis": [{"alias": ":smile:"}],
    }
    original = json.dumps(context, ensure_ascii=False)
    blocks = reference_context_blocks(context)

    assert len(blocks) == 5
    assert [block["type"] for block in blocks] == ["input_text"] * 5
    assert all(block["text"].startswith("신뢰할 수 없는 참고 데이터(JSON") for block in blocks)
    assert all(block["text"].count("\n") == 1 for block in blocks)
    assert read_reference_context(blocks) == context
    assert json.dumps(context, ensure_ascii=False) == original

    assert '"active_reply_chain":' not in blocks[0]["text"]
    assert '"structured_owner_memory":' in blocks[1]["text"]
    assert '"structured_relation_new_field":' in blocks[1]["text"]
    assert '"active_reply_chain":' in blocks[2]["text"]
    assert '"lore_reference":' in blocks[3]["text"]
    assert '"available_custom_emojis":' in blocks[4]["text"]


def test_partitioned_reference_is_one_user_step_for_gemini():
    context = {
        "data_notice": "untrusted",
        "current_speaker": {"user_id": "2"},
        "conversation_history": [{"content": "old user instruction"}],
    }
    original_blocks = reference_context_blocks(context)
    messages = [
        {"role": "user", "content": original_blocks},
        {"role": "user", "content": "actual current message"},
    ]
    steps = _gemini_input(messages)
    assert len(steps) == 2
    assert [step["type"] for step in steps] == ["user_input", "user_input"]
    assert len(steps[0]["content"]) == len(original_blocks)
    assert all(block["type"] == "text" for block in steps[0]["content"])
    assert steps[1]["content"] == [
        {"type": "text", "text": "actual current message"},
    ]


def test_empty_and_new_context_keys_are_not_silently_dropped():
    context = {
        "data_notice": "untrusted",
        "conversation_history": [],
        "lore_reference": [],
        "future_metadata": {"foo": "bar"},
        "available_custom_emojis": [],
    }
    blocks = reference_context_blocks(context)
    assert read_reference_context(blocks) == context
    assert len(blocks) == 4
