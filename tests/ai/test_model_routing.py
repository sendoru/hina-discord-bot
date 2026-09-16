from itertools import pairwise
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
from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS, VisualInput
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
    anchor_source: str = "",
    prior_user_request: str = "",
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
            anchor_source=anchor_source,
            prior_user_request=prior_user_request,
        ),
        route=route,
        references=tuple(references),
        freshness=FreshnessMode.STATIC,
        fact_question=fact_question,
        search_mode=search_mode,
        provenance=ProvenanceMode.SILENT,
    )


def visuals(
    count: int,
    *,
    source: str = "attachment",
    reference_strength: str = "current_message",
):
    return tuple(
        NS(source=source, reference_strength=reference_strength)
        for _ in range(count)
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
    baseline = build_model_plan(
        settings(), information("봐줘"), visual_inputs=visuals(4)
    )
    lowered = build_model_plan(
        settings(model_routing_smart_threshold=0.9),
        information("봐줘"),
        visual_inputs=visuals(4),
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
    visual = build_model_plan(
        settings(), information("이거 뭐야?"), visual_inputs=visuals(1)
    )
    web_and_visual = build_model_plan(
        settings(),
        information("이 설정이 맞아?", search_mode="required"),
        visual_inputs=visuals(1),
    )
    several_signals = build_model_plan(
        settings(),
        information("a? b? c? d? e?", search_mode="required"),
        visual_inputs=visuals(4),
    )

    assert web.tier == ModelTier.FAST
    assert web.score == pytest.approx(0.5)
    assert visual.tier == ModelTier.FAST
    assert visual.score == pytest.approx(0.5)
    assert web_and_visual.tier == ModelTier.FAST
    assert web_and_visual.score == pytest.approx(1.0)
    assert several_signals.tier == ModelTier.SMART
    assert several_signals.score == pytest.approx(2.136)


def test_visible_input_length_uses_soft_curve_without_a_dead_zone():
    tiny = build_model_plan(settings(), information("x" * 100))
    below_old_boundary = build_model_plan(settings(), information("x" * 499))
    above_old_boundary = build_model_plan(settings(), information("x" * 501))
    medium = build_model_plan(settings(), information("x" * 1500))
    long = build_model_plan(settings(), information("x" * 2500))
    full = build_model_plan(settings(), information("x" * 4000))
    saturated = build_model_plan(settings(), information("x" * 5000))

    assert tiny.score == pytest.approx(0.01)
    assert below_old_boundary.score == pytest.approx(0.227)
    assert above_old_boundary.score == pytest.approx(0.229)
    assert medium.score == pytest.approx(1.141)
    assert medium.tier == ModelTier.FAST
    assert long.score == pytest.approx(1.677)
    assert long.tier == ModelTier.FAST
    assert full.score == pytest.approx(2.0)
    assert full.tier == ModelTier.SMART
    assert saturated.score == pytest.approx(2.0)


def test_visible_input_length_has_diminishing_gains_after_the_curve_knee():
    scores = [
        build_model_plan(settings(), information("x" * length)).score
        for length in (1500, 2000, 2500, 3000, 3500)
    ]
    gains = [right - left for left, right in pairwise(scores)]

    assert scores == sorted(scores)
    assert gains == sorted(gains, reverse=True)


@pytest.mark.parametrize(
    "text",
    [
        "비교적 괜찮아?",
        "코드가 뭐야?",
        "'알고리즘'을 영어로 뭐라고 해?",
        "분석하지 말고 결론만 말해줘",
        "자세히 말하지 말고 한 줄로 답해줘",
        "추천 20개만 한 줄씩 적어줘",
    ],
)
def test_keywords_without_an_affirmative_task_stay_fast(text):
    plan = build_model_plan(settings(), information(text))

    assert plan.tier == ModelTier.FAST
    assert "complex_request" not in plan.reasons
    assert "long_answer_requested" not in plan.reasons


@pytest.mark.parametrize(
    "text",
    [
        "adaptive model routing 로직을 살펴보고 개선할 점이 있는지 찾아봐 줘",
        "오류가 나는데 원인을 찾아서 고쳐줘",
        "이 알고리즘의 시간 복잡도를 증명해 줘",
        "이 구현의 장단점을 비교하고 병목을 분석해 줘",
    ],
)
def test_affirmative_complex_tasks_use_smart_tier(text):
    plan = build_model_plan(settings(), information(text))

    assert plan.tier == ModelTier.SMART
    assert "complex_request" in plan.reasons


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


def test_complex_own_request_keeps_smart_tier_for_short_followup():
    plan = build_model_plan(
        settings(),
        information(
            "왜?",
            routing_query="이 알고리즘의 시간 복잡도를 증명해 줘\n왜?",
            anchor="귀납법으로 증명할 수 있어.",
            anchor_source="explicit_reply",
            prior_user_request="이 알고리즘의 시간 복잡도를 증명해 줘",
        ),
    )

    assert plan.tier == ModelTier.SMART
    assert plan.score == pytest.approx(2.0)
    assert plan.reasons == ("complex_followup",)


def test_complex_quoted_source_remains_a_weak_reference_when_own_request_is_simple():
    plan = build_model_plan(
        settings(),
        information(
            "왜?",
            anchor="이 코드를 분석해서 병목을 찾아야 한다",
            anchor_source="explicit_reply",
            prior_user_request="이게 무슨 뜻이야?",
        ),
    )

    assert plan.tier == ModelTier.FAST
    assert plan.score == pytest.approx(0.5)
    assert plan.reasons == ("complex_reference",)


def test_negated_prior_task_does_not_escalate_followup():
    plan = build_model_plan(
        settings(),
        information(
            "왜?",
            anchor="짧게 결론만 말했어.",
            prior_user_request="분석하지 말고 결론만 말해줘",
        ),
    )

    assert plan.tier == ModelTier.FAST
    assert plan.score == 0.0
    assert plan.reasons == ("routine_request",)


def test_followup_route_telemetry_does_not_include_prior_request_text():
    secret = "private-prior-request"
    plan = build_model_plan(
        settings(),
        information(
            "왜?",
            prior_user_request=f"{secret} 오류의 원인을 찾아서 고쳐줘",
        ),
    )
    telemetry = plan.telemetry()

    assert plan.tier == ModelTier.SMART
    assert secret not in str(telemetry)
    assert telemetry["model_route_components"] == {
        "complex_followup": pytest.approx(2.0)
    }


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
    one_visual = build_model_plan(
        settings(), information("봐줘"), visual_inputs=visuals(1)
    )
    four_visuals = build_model_plan(
        settings(), information("봐줘"), visual_inputs=visuals(4)
    )
    eight_visuals = build_model_plan(
        settings(), information("봐줘"), visual_inputs=visuals(8)
    )

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


def test_visual_routing_weights_source_type_and_reference_strength():
    attachment = build_model_plan(
        settings(), information("봐줘"), visual_inputs=visuals(1)
    )
    explicit_reply = build_model_plan(
        settings(),
        information("봐줘"),
        visual_inputs=visuals(1, reference_strength="explicit_reply"),
    )
    sticker = build_model_plan(
        settings(), information("봐줘"), visual_inputs=visuals(1, source="sticker")
    )
    emoji = build_model_plan(
        settings(), information("봐줘"), visual_inputs=visuals(1, source="emoji")
    )
    passive = build_model_plan(
        settings(),
        information("봐줘"),
        visual_inputs=visuals(1, reference_strength="passive_recent"),
    )

    assert attachment.score == pytest.approx(0.5)
    assert explicit_reply.score == pytest.approx(0.5)
    assert sticker.score == pytest.approx(0.312)
    assert emoji.score == pytest.approx(0.179)
    assert passive.score == pytest.approx(0.1)
    assert attachment.reasons == ("strong_visual_input",)
    assert passive.reasons == ("passive_visual_context",)


def test_passive_recent_visuals_stay_bounded_below_a_tier_decision():
    one = build_model_plan(
        settings(),
        information("봐줘"),
        visual_inputs=visuals(1, reference_strength="passive_recent"),
    )
    many = build_model_plan(
        settings(),
        information("봐줘"),
        visual_inputs=visuals(100, reference_strength="passive_recent"),
    )

    assert one.score < many.score < 0.3
    assert many.tier == ModelTier.FAST


def test_visual_route_telemetry_does_not_include_visual_metadata():
    secret = "private-image-name"
    visual = NS(
        source="attachment",
        reference_strength="current_message",
        name=secret,
        message_id="987654321",
        author_name="private-author",
    )
    plan = build_model_plan(
        settings(), information("봐줘"), visual_inputs=(visual,)
    )
    telemetry = plan.telemetry()

    assert secret not in str(telemetry)
    assert visual.message_id not in str(telemetry)
    assert visual.author_name not in str(telemetry)
    assert telemetry["model_route_components"] == {
        "strong_visual_input": pytest.approx(0.5)
    }


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
    plan = build_model_plan(
        settings(), information(secret), visual_inputs=visuals(4)
    )
    telemetry = plan.telemetry()

    assert secret not in str(telemetry)
    assert telemetry["model_tier"] == "fast"
    assert telemetry["model_route_score"] == pytest.approx(0.909)
    assert telemetry["model_route_threshold"] == pytest.approx(2.0)
    assert telemetry["model_route_margin"] == pytest.approx(-1.091)
    assert telemetry["model_route_policy"] == "chat-v3"
    assert sum(telemetry["model_route_components"].values()) == pytest.approx(
        telemetry["model_route_score"]
    )
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


@pytest.mark.asyncio
async def test_runtime_forwards_visual_provenance_to_model_router(monkeypatch):
    observed = {}
    original_build_model_plan = build_model_plan

    def capture_model_plan(settings_value, information_value, **kwargs):
        observed["visual_inputs"] = kwargs.get("visual_inputs")
        return original_build_model_plan(settings_value, information_value, **kwargs)

    monkeypatch.setattr(
        "hina_bot.ai.information_pipeline.build_model_plan",
        capture_model_plan,
    )
    response = NS(status="completed", output_text="응.", output=[], usage=None)
    raw = NS(
        provider_name="gemini",
        responses=NS(create=AsyncMock(return_value=response)),
        close=AsyncMock(),
    )
    llm = LLM(settings(usage_log_path="", chat_web_search=False), client=raw)
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    visual = VisualInput(
        data=b"image",
        mime_type="image/png",
        source="sticker",
        reference_strength="passive_recent",
    )
    token = CURRENT_VISUAL_INPUTS.set((visual,))
    try:
        await llm.answer(store, Scope(None, 10, 100), "사용자", "이거 뭐야?")
        assert observed["visual_inputs"] == (visual,)
    finally:
        CURRENT_VISUAL_INPUTS.reset(token)
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_runtime_distinguishes_owned_followup_from_external_reply_reference():
    response = NS(status="completed", output_text="응.", output=[], usage=None)
    raw = NS(
        provider_name="gemini",
        responses=NS(create=AsyncMock(return_value=response)),
        close=AsyncMock(),
    )
    llm = LLM(
        settings(
            usage_log_path="",
            chat_web_search=False,
            external_context_policy="full",
        ),
        client=raw,
    )
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    store.add(scope, 1, "이 알고리즘의 시간 복잡도를 증명해 줘", "설명")
    try:
        await llm.answer(store, scope, "사용자", "왜?")
        assert raw.responses.create.await_args.kwargs["model"] == "gemini-smart"

        await llm.answer(
            store,
            scope,
            "사용자",
            "왜?",
            channel_context=[{
                "role": "user",
                "author_user_id": "200",
                "content": "이 코드를 분석해서 병목을 찾아야 한다",
                "context_kind": "replied_message",
            }],
        )
        assert raw.responses.create.await_args.kwargs["model"] == "gemini-fast"
    finally:
        await llm.close()
        store.close()
