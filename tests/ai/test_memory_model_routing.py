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
    }
    values.update(overrides)
    return NS(**values)


def _turn(content: str, reply: str = ""):
    row = {"at": "now", "user": content}
    if reply:
        row["hina"] = reply
    return row


def test_fixed_memory_routing_uses_fixed_model_with_memory_budget():
    plan = build_memory_model_plan(
        _settings(model_routing_mode="fixed"),
        "x" * 1800,
        [_turn("큰 입력" * 1000)],
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
    )
    assert plan.tier == ModelTier.FAST
    assert plan.model == "fast-model"
    assert plan.thinking_level == "minimal"
    assert plan.max_output_tokens == 4096
    assert plan.score < 2.0


def test_near_capacity_memory_alone_can_require_smart_compaction():
    plan = build_memory_model_plan(
        _settings(),
        "기" * 1800,
        [_turn("짧음")],
    )
    assert plan.tier == ModelTier.SMART
    assert plan.score >= 2.0
    assert dict(plan.components)["capacity_load"] == pytest.approx(2.0)


def test_large_pending_payload_alone_can_require_smart_compaction():
    plan = build_memory_model_plan(
        _settings(),
        "",
        [_turn("새 정보" * 1500)],
    )
    assert plan.tier == ModelTier.SMART
    assert dict(plan.components)["pending_load"] == pytest.approx(2.0)


def test_moderate_capacity_and_pending_load_combine_without_bonus_rule():
    plan = build_memory_model_plan(
        _settings(),
        "기" * 1350,
        [_turn("새 정보" * 500)],
    )
    components = dict(plan.components)
    assert set(components) <= {"capacity_load", "pending_load"}
    assert plan.score == pytest.approx(sum(components.values()))
    assert "compaction_pressure" not in components


def test_semantic_wording_does_not_change_memory_route_at_same_payload_size():
    routine = build_memory_model_plan(
        _settings(),
        "기억" * 200,
        [_turn("평범한 내용입니다." * 20)],
    )
    update = build_memory_model_plan(
        _settings(),
        "기억" * 200,
        [_turn("앞으로는 설정을 바꾸고 기존 선호는 취소할게." * 8)],
    )
    assert "memory_update_signal" not in dict(routine.components)
    assert "memory_update_signal" not in dict(update.components)


def test_shared_scope_is_expressed_by_target_size_not_a_discount_component():
    settings = _settings()
    previous = "기" * 1000
    pending = [_turn("공개 직접 호출" * 20)]

    personal = build_memory_model_plan(settings, previous, pending, shared=False)
    shared = build_memory_model_plan(settings, previous, pending, shared=True)

    assert dict(shared.components)["capacity_load"] > dict(personal.components)["capacity_load"]
    assert "shared_scope_discount" not in dict(shared.components)


def test_memory_route_components_explain_score_without_content():
    secret = "do-not-log-this"
    plan = build_memory_model_plan(
        _settings(),
        "기" * 1500,
        [_turn(secret * 50)],
        shared=True,
    )
    telemetry = plan.telemetry()

    assert secret not in str(telemetry)
    assert telemetry["model_route_policy"] == "memory-v2"
    assert set(telemetry["model_route_components"]) <= {"capacity_load", "pending_load"}
    assert sum(telemetry["model_route_components"].values()) == pytest.approx(
        telemetry["model_route_score"]
    )
