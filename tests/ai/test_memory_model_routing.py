from types import SimpleNamespace as NS

import pytest

from hina_bot.ai.memory_model_routing import build_memory_model_plan
from hina_bot.ai.model_routing import ModelTier


def _settings(**overrides):
    values = {
        "model": "fixed-model",
        "model_routing_mode": "adaptive",
        "memory_routing_smart_threshold": 2.0,
        "fast_model": "fast-model",
        "smart_model": "smart-model",
        "memory_output_tokens": 4096,
        "gemini_thinking_level": "low",
        "gemini_fast_thinking_level": "minimal",
        "gemini_smart_thinking_level": "medium",
        "summary_every": 8,
    }
    values.update(overrides)
    return NS(**values)


def _turn(content: str, reply: str = ""):
    return {"content": content, "reply": reply}


def test_fixed_memory_routing_uses_fixed_chat_model_with_memory_budget():
    plan = build_memory_model_plan(
        _settings(model_routing_mode="fixed"),
        "x" * 1800,
        [_turn("앞으로는 설정을 바꿔줘" * 100)],
        include_replies=True,
    )
    assert plan.tier == ModelTier.FIXED
    assert plan.model == "fixed-model"
    assert plan.max_output_tokens == 4096
    assert plan.thinking_level == "low"
    assert plan.reasons == ("fixed_mode",)


def test_routine_memory_update_stays_fast():
    plan = build_memory_model_plan(
        _settings(),
        "짧은 기존 기억",
        [_turn("오늘은 그냥 평범한 대화였어", "응") for _ in range(8)],
        include_replies=True,
    )
    assert plan.tier == ModelTier.FAST
    assert plan.model == "fast-model"
    assert plan.thinking_level == "minimal"
    assert plan.max_output_tokens == 4096


def test_near_capacity_memory_with_large_pending_input_uses_smart():
    plan = build_memory_model_plan(
        _settings(),
        "기" * 1700,
        [_turn("새 정보" * 250, "응답" * 100) for _ in range(8)],
        include_replies=True,
    )
    assert plan.tier == ModelTier.SMART
    assert plan.model == "smart-model"
    assert plan.thinking_level == "medium"
    assert "memory_capacity_pressure" in plan.reasons
    assert "pending_input_volume" in plan.reasons
    assert "compaction_pressure" in plan.reasons


def test_explicit_memory_updates_raise_score_without_forcing_smart_alone():
    routine = build_memory_model_plan(
        _settings(),
        "기억" * 300,
        [_turn("평범한 대화") for _ in range(8)],
        include_replies=True,
    )
    updated = build_memory_model_plan(
        _settings(),
        "기억" * 300,
        [_turn("앞으로는 호칭을 바꿔줘. 기존 설정은 취소할게.") for _ in range(8)],
        include_replies=True,
    )
    assert updated.score > routine.score
    assert "memory_update_signal" in updated.reasons


def test_shared_scope_discount_reduces_same_input_score():
    settings = _settings()
    pending = [_turn("공개 직접 호출" * 80) for _ in range(8)]
    personal = build_memory_model_plan(
        settings, "", pending, shared=False, include_replies=True
    )
    shared = build_memory_model_plan(
        settings, "", pending, shared=True, include_replies=False
    )
    assert shared.score < personal.score
    assert "shared_scope_discount" in shared.reasons


def test_extra_pending_turns_add_bounded_pressure():
    settings = _settings()
    normal = build_memory_model_plan(
        settings,
        "",
        [_turn("짧음") for _ in range(8)],
        include_replies=True,
    )
    backed_up = build_memory_model_plan(
        settings,
        "",
        [_turn("짧음") for _ in range(20)],
        include_replies=True,
    )
    assert backed_up.score > normal.score
    assert "extra_pending_turns" in backed_up.reasons


def test_reply_volume_only_counts_when_the_summary_payload_includes_replies():
    pending = [_turn("짧은 사용자 발화", "긴 봇 답변" * 100) for _ in range(8)]
    direct_message = build_memory_model_plan(
        _settings(),
        "기" * 1700,
        pending,
        shared=False,
        include_replies=True,
    )
    server_personal = build_memory_model_plan(
        _settings(),
        "기" * 1700,
        pending,
        shared=False,
        include_replies=False,
    )

    assert direct_message.tier == ModelTier.SMART
    assert "pending_input_volume" in direct_message.reasons
    assert "compaction_pressure" in direct_message.reasons
    assert server_personal.tier == ModelTier.FAST
    assert "pending_input_volume" not in server_personal.reasons
    assert "compaction_pressure" not in server_personal.reasons


def test_memory_route_components_explain_score_without_content():
    secret = "do-not-log-this"
    plan = build_memory_model_plan(
        _settings(),
        "기" * 1500,
        [_turn(secret) for _ in range(8)],
        shared=True,
        include_replies=False,
    )
    telemetry = plan.telemetry()

    assert secret not in str(telemetry)
    assert telemetry["model_route_policy"] == "memory-v1"
    assert sum(telemetry["model_route_components"].values()) == pytest.approx(
        telemetry["model_route_score"]
    )
