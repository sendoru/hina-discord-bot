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


def visuals(count: int, *, source="attachment", reference_strength="current_message"):
    return tuple(
        NS(source=source, reference_strength=reference_strength)
        for _ in range(count)
    )


def test_routine_chat_uses_fast_tier_and_four_physical_loads():
    plan = build_model_plan(settings(), information("오늘 뭐 먹지?"))

    assert plan.tier == ModelTier.FAST
    assert plan.model == "gemini-fast"
    assert plan.max_output_tokens == 4096
    assert plan.thinking_level == "minimal"
    assert plan.score == 0.0
    assert plan.smart_threshold == 2.0
    assert plan.reasons == ("routine_request",)
    assert dict(plan.components) == {
        "request_load": 0.0,
        "context_load": 0.0,
        "evidence_load": 0.0,
        "visual_load": 0.0,
        "semantic_score": 0.0,
    }


def test_smart_threshold_changes_tier_without_reweighting_loads():
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

    assert baseline.score == pytest.approx(1.0)
    assert baseline.tier == ModelTier.FAST
    assert lowered.score == baseline.score
    assert lowered.tier == ModelTier.SMART
    assert raised.score == pytest.approx(2.0)
    assert raised.tier == ModelTier.FAST


@pytest.mark.parametrize(
    "text",
    [
        "adaptive model routing 로직을 살펴보고 개선할 점이 있는지 찾아봐 줘",
        "오류가 나는데 원인을 찾아서 고쳐줘",
        "이 알고리즘의 시간 복잡도를 증명해 줘",
        "이 구현의 장단점을 비교하고 병목을 분석해 줘",
    ],
)
def test_high_confidence_local_semantic_tasks_use_smart_tier(text):
    plan = build_model_plan(settings(), information(text))

    assert plan.tier == ModelTier.SMART
    assert dict(plan.components)["semantic_score"] == pytest.approx(2.0)


def test_long_answer_expands_budget_without_forcing_smart_model():
    plan = build_model_plan(settings(), information("단계별로 자세히 설명해 줘"))

    assert plan.tier == ModelTier.FAST
    assert plan.model == "gemini-fast"
    assert plan.thinking_level == "minimal"
    assert plan.max_output_tokens == 8192
    assert plan.score < 2.0
    assert "long_answer_budget" in plan.reasons


def test_visible_input_length_keeps_soft_curve_without_dead_zone():
    tiny = build_model_plan(settings(), information("x" * 100))
    below = build_model_plan(settings(), information("x" * 499))
    above = build_model_plan(settings(), information("x" * 501))
    medium = build_model_plan(settings(), information("x" * 1500))
    long = build_model_plan(settings(), information("x" * 2500))
    full = build_model_plan(settings(), information("x" * 4000))

    assert tiny.score == pytest.approx(0.01)
    assert below.score == pytest.approx(0.227)
    assert above.score == pytest.approx(0.229)
    assert medium.score == pytest.approx(1.141)
    assert long.score == pytest.approx(1.677)
    assert full.score == pytest.approx(2.0)
    assert full.tier == ModelTier.SMART


def test_visible_input_length_has_diminishing_gains_after_curve_knee():
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
    ],
)
def test_keywords_without_affirmative_complex_task_stay_fast(text):
    plan = build_model_plan(settings(), information(text))
    assert plan.tier == ModelTier.FAST
    assert dict(plan.components)["semantic_score"] == 0.0


def test_quoted_complex_wording_does_not_change_semantic_score():
    anchor = "이 알고리즘을 분석하면 시간 복잡도가 달라진다는 설명"
    plan = build_model_plan(
        settings(),
        information("왜?", routing_query=anchor + "\n왜?", anchor=anchor),
    )

    assert plan.tier == ModelTier.FAST
    assert dict(plan.components)["semantic_score"] == 0.0


def test_complex_own_request_keeps_high_semantic_hint_for_short_followup():
    plan = build_model_plan(
        settings(),
        information(
            "왜?",
            prior_user_request="이 알고리즘의 시간 복잡도를 증명해 줘",
        ),
    )

    assert plan.tier == ModelTier.SMART
    assert dict(plan.components)["semantic_score"] == pytest.approx(2.0)


def test_negated_prior_task_does_not_escalate_followup():
    plan = build_model_plan(
        settings(),
        information("왜?", prior_user_request="분석하지 말고 결론만 말해줘"),
    )
    assert plan.tier == ModelTier.FAST
    assert dict(plan.components)["semantic_score"] == 0.0


def test_context_load_depends_on_admitted_text_volume_not_context_kind():
    small = build_model_plan(settings(), information("읽어봐"), context_chars=1000)
    medium = build_model_plan(settings(), information("읽어봐"), context_chars=4500)
    large = build_model_plan(settings(), information("읽어봐"), context_chars=8000)

    assert dict(small.components)["context_load"] == 0.0
    assert 0.0 < dict(medium.components)["context_load"] < 2.0
    assert dict(large.components)["context_load"] == pytest.approx(2.0)
    assert large.tier == ModelTier.SMART


def test_evidence_load_uses_actual_reference_text_and_final_web_requirement():
    refs = ({"content": "x" * 2750},)
    local = build_model_plan(settings(), information("설정 알려줘", references=refs))
    web = build_model_plan(
        settings(), information("설정 알려줘", references=refs, search_mode="required")
    )

    assert dict(local.components)["evidence_load"] == pytest.approx(0.5)
    assert dict(web.components)["evidence_load"] == pytest.approx(1.0)
    assert web.score > local.score


def test_visual_load_counts_actual_inputs_without_provenance_taxonomy():
    attachment = build_model_plan(
        settings(), information("봐줘"), visual_inputs=visuals(1)
    )
    passive_sticker = build_model_plan(
        settings(),
        information("봐줘"),
        visual_inputs=visuals(1, source="sticker", reference_strength="passive_recent"),
    )
    four = build_model_plan(settings(), information("봐줘"), visual_inputs=visuals(4))
    many = build_model_plan(settings(), information("봐줘"), visual_inputs=visuals(100))

    assert dict(attachment.components)["visual_load"] == pytest.approx(0.25)
    assert dict(passive_sticker.components)["visual_load"] == pytest.approx(0.25)
    assert dict(four.components)["visual_load"] == pytest.approx(1.0)
    assert dict(many.components)["visual_load"] == pytest.approx(1.0)


def test_semantic_classifier_score_combines_directly_with_physical_load():
    medium = build_model_plan(
        settings(), information("안녕"), semantic_level="medium"
    )
    combined = build_model_plan(
        settings(),
        information("안녕"),
        context_chars=4500,
        semantic_level="medium",
    )
    high = build_model_plan(settings(), information("안녕"), semantic_level="high")

    assert medium.tier == ModelTier.FAST
    assert combined.tier == ModelTier.SMART
    assert high.tier == ModelTier.SMART
    assert dict(high.components)["semantic_score"] == pytest.approx(2.0)


def test_routing_telemetry_contains_no_prompt_or_visual_metadata():
    secret = "do-not-log-this"
    visual = NS(
        source="attachment",
        reference_strength="current_message",
        name="private-image-name",
        message_id="987654321",
        author_name="private-author",
    )
    plan = build_model_plan(
        settings(), information(secret), visual_inputs=(visual,)
    )
    telemetry = plan.telemetry()

    assert secret not in str(telemetry)
    assert visual.name not in str(telemetry)
    assert visual.message_id not in str(telemetry)
    assert telemetry["model_route_policy"] == "chat-v4"
    assert set(telemetry["model_route_components"]) == {
        "request_load", "context_load", "evidence_load", "visual_load", "semantic_score"
    }
    assert sum(telemetry["model_route_components"].values()) == pytest.approx(
        telemetry["model_route_score"]
    )


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


@pytest.mark.asyncio
async def test_runtime_forwards_each_tiers_gemini_budget():
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

        await llm.answer(
            store, Scope(None, 10, 100), "사용자", "이 알고리즘을 단계별로 분석해 줘"
        )
        smart = raw.responses.create.await_args.kwargs
        assert smart["model"] == "gemini-smart"
        assert smart["max_output_tokens"] == 8192
        assert smart["thinking_level"] == "medium"
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_runtime_long_answer_can_keep_fast_model_with_large_budget():
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
        await llm.answer(
            store,
            Scope(None, 10, 100),
            "사용자",
            "벡터가 뭔지 단계별로 자세히 설명해 줘",
        )
        request = raw.responses.create.await_args.kwargs
        assert request["model"] == "gemini-fast"
        assert request["max_output_tokens"] == 8192
        assert request["thinking_level"] == "minimal"
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_runtime_forwards_visuals_to_model_router(monkeypatch):
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
