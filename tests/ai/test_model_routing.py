from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.freshness import FreshnessMode
from hina_bot.ai.information_plan import InformationPlan
from hina_bot.ai.information_routing import InformationRoute
from hina_bot.ai.model_routing import ModelTier, build_model_plan
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.ai.rp_output_policy import ProvenanceMode
from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.lore import LoreIndex
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def settings(**overrides):
    values = {
        "api_key": "key",
        "discord_token": "token",
        "provider": "gemini",
        "model": "legacy",
        "model_routing_mode": "adaptive",
        "fast_model": "gemini-fast",
        "smart_model": "gemini-smart",
    }
    values.update(overrides)
    return Settings(**values)


def information(
    text: str,
    *,
    routing_query: str | None = None,
    anchor: str = "",
    route=InformationRoute.GENERAL,
    search_mode="none",
    fact_question=False,
    references=(),
):
    return InformationPlan(
        routing=RoutingPlan(
            text,
            routing_query if routing_query is not None else text,
            anchor=anchor,
        ),
        route=route,
        references=tuple(references),
        freshness=FreshnessMode.STATIC,
        fact_question=fact_question,
        search_mode=search_mode,
        provenance=ProvenanceMode.SILENT,
    )


def test_routine_chat_uses_fast_tier_and_common_generation_budget():
    plan = build_model_plan(settings(), information("오늘 뭐 먹지?"))

    assert plan.tier == ModelTier.FAST
    assert plan.model == "gemini-fast"
    assert plan.max_output_tokens == 4096
    assert plan.thinking_level == "minimal"
    assert plan.score == 0.0
    assert plan.smart_threshold == 2.0
    assert plan.reasons == ("routine_request",)


def test_smart_threshold_can_change_tier_without_reweighting_signals():
    baseline = build_model_plan(settings(), information("봐줘"), visual_count=4)
    lowered = build_model_plan(
        settings(model_routing_smart_threshold=0.9),
        information("봐줘"),
        visual_count=4,
    )
    raised = build_model_plan(
        settings(model_routing_smart_threshold=2.5),
        information("이 알고리즘을 분석해 줘"),
    )

    assert baseline.score == pytest.approx(0.909)
    assert baseline.tier == ModelTier.FAST
    assert lowered.score == baseline.score
    assert lowered.smart_threshold == pytest.approx(0.9)
    assert lowered.tier == ModelTier.SMART
    assert raised.score == pytest.approx(2.0)
    assert raised.smart_threshold == pytest.approx(2.5)
    assert raised.tier == ModelTier.FAST


def test_strong_semantic_requests_still_select_smart_directly():
    complex_plan = build_model_plan(
        settings(), information("이 알고리즘의 시간 복잡도를 증명해 줘")
    )
    long_plan = build_model_plan(settings(), information("단계별로 자세히 설명해 줘"))

    for plan in (complex_plan, long_plan):
        assert plan.tier == ModelTier.SMART
        assert plan.model == "gemini-smart"
        assert plan.max_output_tokens == 8192
        assert plan.thinking_level == "medium"
        assert plan.score >= 2.0


def test_single_operational_signals_are_weak_and_need_support_to_escalate():
    web = build_model_plan(
        settings(), information("서울 날씨 어때?", search_mode="required")
    )
    visual = build_model_plan(settings(), information("이거 뭐야?"), visual_count=1)
    web_and_visual = build_model_plan(
        settings(),
        information("이 설정이 맞아?", search_mode="required"),
        visual_count=1,
    )
    several_signals = build_model_plan(
        settings(),
        information("a? b? c? d? e?", search_mode="required"),
        visual_count=4,
    )

    assert web.tier == ModelTier.FAST
    assert web.score == pytest.approx(0.5)
    assert visual.tier == ModelTier.FAST
    assert visual.score == pytest.approx(0.5)
    assert web_and_visual.tier == ModelTier.FAST
    assert web_and_visual.score == pytest.approx(1.0)
    assert several_signals.tier == ModelTier.SMART
    assert several_signals.score == pytest.approx(2.136)


def test_visible_input_length_uses_broad_soft_ramp_and_saturates():
    short = build_model_plan(settings(), information("x" * 500))
    medium = build_model_plan(settings(), information("x" * 1500))
    long = build_model_plan(settings(), information("x" * 2500))
    very_long = build_model_plan(settings(), information("x" * 5000))

    assert short.score == 0.0
    assert medium.score == pytest.approx(1.0)
    assert medium.tier == ModelTier.FAST
    assert long.score == pytest.approx(2.0)
    assert long.tier == ModelTier.SMART
    assert very_long.score == pytest.approx(2.0)


def test_quoted_complex_wording_is_not_treated_as_a_direct_complex_request():
    anchor = "이 알고리즘을 분석하면 시간 복잡도가 달라진다는 설명"
    plan = build_model_plan(
        settings(),
        information(
            "왜?",
            routing_query=anchor + "\n왜?",
            anchor=anchor,
        ),
        channel_context=[{"context_kind": "replied_message", "content": "x" * 200}],
    )

    assert plan.tier == ModelTier.FAST
    assert plan.score == pytest.approx(0.5)
    assert plan.reasons == ("complex_reference",)
    assert "complex_request" not in plan.reasons
    assert "input_length" not in plan.reasons


def test_explicit_reply_length_uses_soft_weight_and_long_reply_selects_smart():
    short = [{"context_kind": "replied_message", "content": "x" * 200}]
    medium = [{"context_kind": "replied_message", "content": "x" * 700}]
    substantial = [{"context_kind": "replied_message", "content": "x" * 1200}]
    long = [{"context_kind": "replied_message", "content": "x" * 2000}]

    short_plan = build_model_plan(
        settings(), information("이거 읽어봐"), channel_context=short,
    )
    medium_plan = build_model_plan(
        settings(), information("이거 읽어봐"), channel_context=medium,
    )
    substantial_plan = build_model_plan(
        settings(), information("이거 읽어봐"), channel_context=substantial,
    )
    long_plan = build_model_plan(
        settings(), information("이거 읽어봐"), channel_context=long,
    )

    assert short_plan.tier == ModelTier.FAST
    assert short_plan.score == 0.0
    assert medium_plan.score == pytest.approx(0.471)
    assert medium_plan.tier == ModelTier.FAST
    assert substantial_plan.score == pytest.approx(1.059)
    assert substantial_plan.tier == ModelTier.FAST
    assert long_plan.score == pytest.approx(2.0)
    assert long_plan.tier == ModelTier.SMART
    assert "explicit_reply_length" in long_plan.reasons


def test_visual_and_reference_counts_gain_bounded_diminishing_weight():
    one_visual = build_model_plan(settings(), information("봐줘"), visual_count=1)
    four_visuals = build_model_plan(settings(), information("봐줘"), visual_count=4)
    eight_visuals = build_model_plan(settings(), information("봐줘"), visual_count=8)

    assert one_visual.score == pytest.approx(0.5)
    assert four_visuals.score == pytest.approx(0.909)
    assert eight_visuals.score == pytest.approx(1.053)
    assert one_visual.score < four_visuals.score < eight_visuals.score < 1.25

    two_refs = build_model_plan(
        settings(), information("설정 알려줘", references=({}, {}))
    )
    eight_refs = build_model_plan(
        settings(), information("설정 알려줘", references=tuple({} for _ in range(8)))
    )

    assert two_refs.score == pytest.approx(0.2)
    assert eight_refs.score == pytest.approx(0.56)
    assert two_refs.score < eight_refs.score < 0.8
    assert eight_refs.tier == ModelTier.FAST


def test_multiple_requirements_gain_bounded_diminishing_weight():
    two = build_model_plan(settings(), information("a? b?"))
    five = build_model_plan(settings(), information("a? b? c? d? e?"))

    assert two.score == pytest.approx(0.4)
    assert five.score == pytest.approx(0.727)
    assert two.score < five.score < 1.0
    assert five.tier == ModelTier.FAST


def test_multi_source_and_ambient_context_need_enough_combined_load_for_smart():
    lore_only = build_model_plan(
        settings(),
        information(
            "둘이 만난 적 있어?",
            route=InformationRoute.LOCAL_THEN_WEB,
            search_mode="required",
            fact_question=True,
            references=tuple({} for _ in range(4)),
        ),
    )
    with_medium_context = build_model_plan(
        settings(),
        information(
            "둘이 만난 적 있어?",
            route=InformationRoute.LOCAL_THEN_WEB,
            search_mode="required",
            fact_question=True,
            references=tuple({} for _ in range(4)),
        ),
        channel_context=[{"context_kind": "speaker_thread", "content": "x" * 3000}],
    )
    with_large_context = build_model_plan(
        settings(),
        information(
            "둘이 만난 적 있어?",
            route=InformationRoute.LOCAL_THEN_WEB,
            search_mode="required",
            fact_question=True,
            references=tuple({} for _ in range(4)),
        ),
        channel_context=[{"context_kind": "speaker_thread", "content": "x" * 5000}],
    )

    assert lore_only.score == pytest.approx(1.65)
    assert lore_only.tier == ModelTier.FAST
    assert with_medium_context.score == pytest.approx(1.971)
    assert with_medium_context.tier == ModelTier.FAST
    assert with_large_context.score == pytest.approx(2.4)
    assert with_large_context.tier == ModelTier.SMART


def test_reply_and_target_history_are_not_counted_as_generic_surrounding_context():
    reply_only = build_model_plan(
        settings(),
        information("이거 읽어봐"),
        channel_context=[{"context_kind": "replied_message", "content": "x" * 1200}],
    )
    basic_history = build_model_plan(
        settings(),
        information("방금 뭐라고 했어?"),
        channel_context=[{
            "context_kind": "target_user_history",
            "target_retrieval_mode": "basic",
            "content": "x" * 1200,
        }],
    )

    assert reply_only.score == pytest.approx(1.059)
    assert "surrounding_context_length" not in reply_only.reasons
    assert basic_history.score == pytest.approx(0.614)
    assert "surrounding_context_length" not in basic_history.reasons


def test_target_history_depth_and_volume_combine_without_overweighting_basic_history():
    basic = [{
        "context_kind": "target_user_history",
        "target_retrieval_mode": "basic",
        "content": "방금 한 말",
    }]
    deep_short = [{
        "context_kind": "target_user_history",
        "target_retrieval_mode": "deep",
        "content": "짧은 발언",
    }]
    deep_full = [{
        "context_kind": "target_user_history",
        "target_retrieval_mode": "deep",
        "content": "x" * 2400,
    }]

    basic_plan = build_model_plan(
        settings(), information("방금 뭐라고 했어?"), channel_context=basic,
    )
    deep_short_plan = build_model_plan(
        settings(), information("어떤 사람 같아?"), channel_context=deep_short,
    )
    deep_full_plan = build_model_plan(
        settings(), information("어떤 사람 같아?"), channel_context=deep_full,
    )

    assert basic_plan.tier == ModelTier.FAST
    assert basic_plan.score == pytest.approx(0.4)
    assert deep_short_plan.tier == ModelTier.FAST
    assert deep_short_plan.score == pytest.approx(1.6)
    assert deep_full_plan.tier == ModelTier.SMART
    assert deep_full_plan.score == pytest.approx(2.1)
    assert "target_history_volume" in deep_full_plan.reasons


def test_fixed_mode_uses_common_generation_budget():
    plan = build_model_plan(
        settings(
            model_routing_mode="fixed",
            model="single-model",
            output_tokens=4096,
            gemini_thinking_level="low",
        ),
        information("자세히 분석해 줘"),
    )

    assert plan.tier == ModelTier.FIXED
    assert plan.model == "single-model"
    assert plan.max_output_tokens == 4096
    assert plan.thinking_level == "low"
    assert plan.score == 0.0
    assert plan.smart_threshold == 2.0


def test_routing_telemetry_contains_no_prompt_text_and_accepts_float_score():
    secret = "do-not-log-this"
    plan = build_model_plan(settings(), information(secret), visual_count=4)
    telemetry = plan.telemetry()

    assert secret not in str(telemetry)
    assert telemetry["model_tier"] == "fast"
    assert telemetry["model_route_score"] == pytest.approx(0.909)
    assert telemetry["model_route_threshold"] == pytest.approx(2.0)
    assert telemetry["requested_max_output_tokens"] == 4096
    assert "requested_total_output_tokens" not in telemetry


@pytest.mark.asyncio
async def test_runtime_forwards_each_tiers_common_gemini_budget():
    response = NS(status="completed", output_text="응.", output=[], usage=None)
    raw = NS(
        provider_name="gemini",
        responses=NS(create=AsyncMock(return_value=response)),
        close=AsyncMock(),
    )
    llm = LLM(settings(usage_log_path="", chat_web_search=False), client=raw)
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 10, 100), "사용자", "안녕")
        fast = raw.responses.create.await_args.kwargs
        assert fast["model"] == "gemini-fast"
        assert fast["max_output_tokens"] == 4096
        assert fast["thinking_level"] == "minimal"
        assert "total_output_tokens" not in fast

        await llm.answer(
            store, Scope(None, 10, 100), "사용자", "이 알고리즘을 단계별로 분석해 줘"
        )
        smart = raw.responses.create.await_args.kwargs
        assert smart["model"] == "gemini-smart"
        assert smart["max_output_tokens"] == 8192
        assert smart["thinking_level"] == "medium"
        assert "total_output_tokens" not in smart
    finally:
        await llm.close()
        store.close()
