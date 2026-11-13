from hina_bot.ai.request_assembly import (
    REFERENCE_CONTINUITY_POLICY,
    REFERENCE_PROVENANCE_POLICY,
    REPLY_CONTINUITY_POLICY,
    _reference_instruction_parts,
)


def test_plain_turn_keeps_only_small_continuity_baseline():
    parts = _reference_instruction_parts({
        "active_reply_chain": [],
        "channel_recent_messages": [],
        "personal_recent_conversation": [],
        "conversation_history": [],
    })

    assert parts == (REFERENCE_CONTINUITY_POLICY,)


def test_plain_reply_adds_reply_guidance_without_reference_rules():
    parts = _reference_instruction_parts({
        "active_reply_chain": [{
            "context_kind": "replied_message",
            "provenance_class": "conversation",
        }],
        "channel_recent_messages": [],
        "personal_recent_conversation": [],
        "conversation_history": [],
    })

    assert parts == (
        REFERENCE_CONTINUITY_POLICY,
        REPLY_CONTINUITY_POLICY,
    )


def test_carried_reference_adds_provenance_guidance_without_active_reply():
    parts = _reference_instruction_parts({
        "active_reply_chain": [],
        "channel_recent_messages": [{
            "context_kind": "prior_reply_source",
            "provenance_class": "reference_material",
        }],
        "personal_recent_conversation": [],
        "conversation_history": [],
    })

    assert parts == (
        REFERENCE_CONTINUITY_POLICY,
        REFERENCE_PROVENANCE_POLICY,
    )


def test_reference_derived_reply_uses_both_conditional_policies():
    parts = _reference_instruction_parts({
        "active_reply_chain": [{
            "context_kind": "replied_message",
            "provenance_class": "reference_derived",
        }],
        "channel_recent_messages": [],
        "personal_recent_conversation": [],
        "conversation_history": [],
    })

    assert parts == (
        REFERENCE_CONTINUITY_POLICY,
        REPLY_CONTINUITY_POLICY,
        REFERENCE_PROVENANCE_POLICY,
    )


def test_continuity_prioritizes_recent_same_speaker_dialogue_without_tone_lock_in():
    # Static prompt contract, not a substitute for live Gemini quality checks.
    assert "DM에서는 conversation_history의 최근 historical_exchange" in (
        REFERENCE_CONTINUITY_POLICY
    )
    assert "서버에서는 speaker_thread와 personal_recent_conversation" in (
        REFERENCE_CONTINUITY_POLICY
    )
    assert "관련 없는 장기기억의 화제를 대신 꺼내지 마세요" in (
        REFERENCE_CONTINUITY_POLICY
    )
    assert "현재 발화가 새 주제나 새 대상을 명시하면" in (
        REFERENCE_CONTINUITY_POLICY
    )
    assert "다른 사용자와의 대화로 인한 감정" in (
        REFERENCE_CONTINUITY_POLICY
    )
    assert "at_local/elapsed_minutes" in REFERENCE_CONTINUITY_POLICY
