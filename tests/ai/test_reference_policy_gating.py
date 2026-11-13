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


def test_contextual_reference_resolution_prefers_natural_flow_without_guessing_facts():
    assert "출처별 고정 순위로 기계적으로 정하지 말고" in REFERENCE_CONTINUITY_POLICY
    assert "channel_ambient도 현재 화제의 중요한 단서" in REFERENCE_CONTINUITY_POLICY
    assert "대명사·생략된 주어·목적어·암묵적 지칭" in REFERENCE_CONTINUITY_POLICY
    assert "정확한 대상을 지정하지 않고도 자연스럽게 반응" in REFERENCE_CONTINUITY_POLICY
    assert "실질적인 후보가 여럿이며 답변이 달라질 때만" in REFERENCE_CONTINUITY_POLICY
    assert "불분명한 행동·관계·상태를 사실로 확정하지는 마세요" in REFERENCE_CONTINUITY_POLICY
    assert "이전 히나 답변은 대화 흐름의 근거이지" in REFERENCE_CONTINUITY_POLICY


def test_dialogue_attribution_keeps_topic_link_separate_from_factual_claims():
    from hina_bot.ai.request_assembly import DIALOGUE_CONTEXT_POLICY

    assert "channel_ambient라는 이유만으로 관련 없는 이야기" in DIALOGUE_CONTEXT_POLICY
    assert "발화의 작성자, 발화가 향하는 상대, 내용에서 지칭하는 인물" in (
        DIALOGUE_CONTEXT_POLICY
    )
    assert "각 주장의 출처" in DIALOGUE_CONTEXT_POLICY
    assert "대화가 무엇을 이어받는지 문맥으로 해석하는 것과" in DIALOGUE_CONTEXT_POLICY
    assert "사실 여부는 명시적 근거에 따라 판단" in DIALOGUE_CONTEXT_POLICY
