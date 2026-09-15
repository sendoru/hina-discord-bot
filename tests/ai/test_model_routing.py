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
    route=InformationRoute.GENERAL,
    search_mode="none",
    fact_question=False,
    references=(),
):
    return InformationPlan(
        routing=RoutingPlan(text, text),
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
    assert plan.reasons == ("routine_request",)


def test_complex_or_explicitly_long_request_uses_smart_tier():
    complex_plan = build_model_plan(
        settings(), information("이 알고리즘의 시간 복잡도를 증명해 줘")
    )
    long_plan = build_model_plan(settings(), information("단계별로 자세히 설명해 줘"))

    for plan in (complex_plan, long_plan):
        assert plan.tier == ModelTier.SMART
        assert plan.model == "gemini-smart"
        assert plan.max_output_tokens == 8192
        assert plan.thinking_level == "medium"


def test_one_weak_signal_stays_fast_but_combined_signals_escalate():
    weather = build_model_plan(
        settings(), information("서울 날씨 어때?", search_mode="required")
    )
    visual = build_model_plan(settings(), information("이거 뭐야?"), visual_count=1)
    combined = build_model_plan(
        settings(),
        information("이 설정이 맞아?", search_mode="required"),
        visual_count=1,
    )

    assert weather.tier == ModelTier.FAST
    assert visual.tier == ModelTier.FAST
    assert combined.tier == ModelTier.SMART


def test_lore_synthesis_and_large_relevant_context_can_escalate():
    lore = build_model_plan(
        settings(),
        information(
            "둘이 만난 적 있어?",
            route=InformationRoute.LOCAL_THEN_WEB,
            search_mode="required",
            fact_question=True,
        ),
    )
    context = [{"context_kind": "speaker_thread", "content": "x" * 3000}]
    context_plan = build_model_plan(
        settings(), information("이어서 말해 줘", search_mode="required"),
        channel_context=context,
    )

    assert lore.tier == ModelTier.SMART
    assert context_plan.tier == ModelTier.SMART


def test_target_history_depth_participates_in_model_routing():
    basic = [{
        "context_kind": "target_user_history",
        "target_retrieval_mode": "basic",
        "content": "방금 한 말",
    }]
    deep = [{
        "context_kind": "target_user_history",
        "target_retrieval_mode": "deep",
        "content": "분석할 발언",
    }]

    assert build_model_plan(
        settings(), information("방금 뭐라고 했어?"), channel_context=basic,
    ).tier == ModelTier.FAST
    assert build_model_plan(
        settings(), information("어떤 사람 같아?"), channel_context=deep,
    ).tier == ModelTier.SMART


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


def test_routing_telemetry_contains_no_prompt_text():
    secret = "do-not-log-this"
    plan = build_model_plan(settings(), information(secret))

    assert secret not in str(plan.telemetry())
    assert plan.telemetry()["model_tier"] == "fast"
    assert plan.telemetry()["requested_max_output_tokens"] == 4096
    assert "requested_total_output_tokens" not in plan.telemetry()


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
