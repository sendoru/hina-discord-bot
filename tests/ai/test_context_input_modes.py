"""Regression tests for structured context and text-only Gemini DM replay."""
from __future__ import annotations

from hina_bot.ai.context_input import build_context_input
from hina_bot.ai.providers import _gemini_generate_content_contents, _gemini_input
from hina_bot.ai.reference_context_blocks import read_reference_context


def context():
    return {
        "data_notice": "untrusted reference data",
        "current_speaker": {"user_id": "100"},
        "current_interaction": {"speaker": {"user_id": "100"}, "reply_target": {"user_id": "9"}},
        "space": "DM",
        "server_note": "keep note",
        "active_reply_chain": [
            {"context_kind": "replied_message", "role": "assistant",
             "reply_target_user_id": "100", "content": "그건 네 얘기였어?"}
        ],
        "channel_recent_messages": [{"role": "user", "author_user_id": "200", "content": "other"}],
        "conversation_history": [
            {"role": "user", "at": "2026-11-11T11:00:00Z", "content": "머리가 너무 커"},
            {"role": "assistant", "at": "2026-11-11T11:00:00Z",
             "content": "또 나를 놀리는 거야?"},
        ],
        "personal_recent_conversation": [],
        "public_server_context": [],
    }


def merged_references(steps):
    blocks = [
        block
        for step in steps
        if step["role"] == "user" and isinstance(step["content"], list)
        for block in step["content"]
        if block["type"] == "input_text"
        and block["text"].startswith("신뢰할 수 없는 참고 데이터(JSON")
    ]
    return read_reference_context(blocks)


def test_legacy_is_the_original_two_user_steps():
    payload = build_context_input(context(), "내 머리가 너무 크다구 너말고")
    assert [step["role"] for step in payload] == ["user", "user"]
    assert payload[-1]["content"] == "내 머리가 너무 크다구 너말고"
    assert merged_references(payload)["conversation_history"][0]["user_message"]["content"] == "머리가 너무 커"


def test_structured_moves_current_interaction_and_reply_next_to_current_text():
    payload = build_context_input(context(), "정정할게", mode="structured", is_dm=True)
    assert [step["role"] for step in payload] == ["user", "user"]
    background = read_reference_context(payload[0]["content"])
    current = read_reference_context(payload[1]["content"][:-1])
    assert "current_interaction" not in background
    assert "active_reply_chain" not in background
    assert current["current_speaker"]["user_id"] == "100"
    assert current["active_reply_chain"][0]["content"] == "그건 네 얘기였어?"
    assert payload[-1]["content"][-1] == {"type": "input_text", "text": "정정할게"}
    assert merged_references(payload)["conversation_history"][0]["user_message"]["content"] == "머리가 너무 커"
    # B repositions existing fields only, without duplicating or editing the source.
    original = context()
    assert set(merged_references(payload)) == set(original)
    assert merged_references(payload)["channel_recent_messages"][0]["author_user_id"] == "200"
    assert original["current_interaction"]["reply_target"]["user_id"] == "9"


def test_dm_replay_uses_roles_and_does_not_repeat_history_in_json():
    payload = build_context_input(
        context(), "내 머리가 너무 크다구 너말고",
        mode="structured_with_replay", provider="gemini", is_dm=True,
    )
    assert [step["role"] for step in payload] == ["user", "user", "assistant", "user"]
    assert payload[1]["content"] == "머리가 너무 커"
    assert payload[2]["content"] == "또 나를 놀리는 거야?"
    assert read_reference_context(payload[0]["content"])["conversation_history"] == []
    assert merged_references(payload)["active_reply_chain"][0]["role"] == "assistant"
    assert [step["type"] for step in _gemini_input(payload)] == [
        "user_input", "user_input", "model_output", "user_input"
    ]
    assert [step["role"] for step in _gemini_generate_content_contents(payload)] == [
        "user", "model", "user"
    ]


def test_replay_is_dm_and_gemini_only():
    for is_dm, provider in [(False, "gemini"), (True, "openai")]:
        payload = build_context_input(
            context(), "현재 메시지",
            mode="structured_with_replay", provider=provider, is_dm=is_dm,
        )
        assert [step["role"] for step in payload] == ["user", "user"]
        assert merged_references(payload)["conversation_history"][0]["user_message"]["content"] == "머리가 너무 커"


def test_history_lookup_or_incomplete_exchange_not_replayed():
    payload_context = context()
    payload_context["conversation_history"][1]["provenance_class"] = "reference_derived"
    payload = build_context_input(
        payload_context, "현재",
        mode="structured_with_replay", provider="gemini", is_dm=True,
    )
    assert [step["role"] for step in payload] == ["user", "user"]
    assert len(read_reference_context(payload[0]["content"])["conversation_history"]) == 1
    payload_context["conversation_history"] = [
        {"role": "assistant", "at": "2026-11-11T10:00:00Z", "content": "orphan"},
        {"role": "user", "at": "2026-11-11T11:00:00Z", "content": "new"},
        {"role": "assistant", "at": "2026-11-11T11:00:00Z", "content": "reply"},
    ]
    payload = build_context_input(
        payload_context, "현재",
        mode="structured_with_replay", provider="gemini", is_dm=True,
    )
    assert [step["role"] for step in payload] == ["user", "user", "assistant", "user"]
    assert len(read_reference_context(payload[0]["content"])["conversation_history"]) == 1


def test_unknown_mode_rejected():
    import pytest
    with pytest.raises(ValueError, match="Unsupported context input mode"):
        build_context_input(context(), "현재", mode="wrong")
