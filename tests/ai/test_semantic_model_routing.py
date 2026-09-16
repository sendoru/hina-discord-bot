import asyncio
import json
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
from hina_bot.ai.semantic_model_routing import (
    InvalidClassifierResponse,
    SemanticClassification,
    SemanticModelRouter,
    parse_classification,
    semantic_result,
)
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.config import Settings
from hina_bot.core.lore import LoreIndex
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def settings(**overrides):
    values = {
        "api_key": "primary-key",
        "discord_token": "token",
        "provider": "gemini",
        "model": "fixed",
        "model_routing_mode": "adaptive",
        "fast_model": "fast",
        "smart_model": "smart",
        "routing_classifier_mode": "active",
        "routing_classifier_provider": "gemini",
        "routing_classifier_model": "classifier",
        "routing_classifier_api_key": "classifier-key",
        "usage_log_path": "",
        "chat_web_search": False,
    }
    values.update(overrides)
    return Settings(**values)


def information(text, *, prior="", anchor=""):
    return InformationPlan(
        routing=RoutingPlan(
            text,
            text,
            anchor=anchor,
            anchor_source="external_reply" if anchor else "",
            prior_user_request=prior,
        ),
        route=InformationRoute.GENERAL,
        references=(),
        freshness=FreshnessMode.STATIC,
        fact_question=False,
        search_mode="none",
        provenance=ProvenanceMode.SILENT,
        search_locked=True,
    )


def classification(*, level="low", uncertain=False, web_need="none", web_uncertain=False):
    return json.dumps({
        "level": level,
        "codes": ["simple_chat" if level == "low" else "multi_step_reasoning"],
        "uncertain": uncertain,
        "web_need": web_need,
        "web_codes": ["stable_or_contextual"],
        "web_uncertain": web_uncertain,
    })


def response(text, *, status="completed"):
    return NS(status=status, output_text=text, output=[], usage=None)


def client(result):
    return NS(
        provider_name="gemini",
        responses=NS(create=AsyncMock(return_value=result)),
        close=AsyncMock(),
    )


def test_classifier_parser_accepts_only_current_bounded_schema():
    parsed = parse_classification(classification(level="high"))
    assert parsed == SemanticClassification(
        "high",
        ("multi_step_reasoning",),
        False,
        "none",
        ("stable_or_contextual",),
        False,
    )

    fenced = parse_classification("```json\n" + classification() + "\n```")
    assert fenced.level == "low"

    invalid_level = (
        '{"level":"critical","codes":["debugging"],"uncertain":false,'
        '"web_need":"none","web_codes":["stable_or_contextual"],'
        '"web_uncertain":false}'
    )
    for invalid in (
        "not-json",
        '{"level":"high","codes":["debugging"],"uncertain":false}',
        invalid_level,
    ):
        with pytest.raises(InvalidClassifierResponse):
            parse_classification(invalid)


def test_semantic_score_combines_directly_with_physical_load():
    config = settings()
    low = build_model_plan(config, information("안녕"), semantic_level="low")
    medium = build_model_plan(config, information("안녕"), semantic_level="medium")
    high = build_model_plan(config, information("안녕"), semantic_level="high")
    combined = build_model_plan(
        config,
        information("안녕"),
        context_chars=4500,
        semantic_level="medium",
    )

    assert low.tier == ModelTier.FAST
    assert medium.tier == ModelTier.FAST
    assert high.tier == ModelTier.SMART
    assert dict(high.components)["semantic_score"] == pytest.approx(2.0)
    assert combined.tier == ModelTier.SMART
    assert "model_route_objective_axes" not in combined.telemetry()
    assert "model_route_objective_bands" not in combined.telemetry()


def test_uncertain_or_failed_classifier_exposes_no_semantic_score():
    uncertain = parse_classification(classification(level="high", uncertain=True))
    level, codes, status = semantic_result(NS(status="completed", result=uncertain))
    assert level == ""
    assert codes
    assert status == "uncertain"

    level, codes, status = semantic_result(NS(status="timeout", result=None))
    assert level == ""
    assert codes == ()
    assert status == "timeout"


@pytest.mark.asyncio
async def test_classifier_input_contains_semantic_context_but_no_objective_load():
    config = settings()
    classifier_client = client(response(classification(level="medium")))
    router = SemanticModelRouter(config, classifier_client, UsageLogger(""))
    info = information(
        "그 조건까지 고려하면?",
        prior="내가 앞서 요청한 내용",
        anchor="third-party-secret",
    )

    outcome = await router.classify(info, baseline_tier="fast")

    assert outcome.status == "completed"
    request = classifier_client.responses.create.await_args.kwargs
    payload = json.loads(request["input"])
    assert payload["current_request"] == "그 조건까지 고려하면?"
    assert payload["prior_user_request"] == "내가 앞서 요청한 내용"
    assert payload["anchor"] == {"source": "external_reply", "text": ""}
    assert payload["context_signals"]["anchor_present"] is True
    assert payload["context_signals"]["anchor_included"] is False
    assert "objective_load" not in payload
    assert "third-party-secret" not in request["input"]
    assert request["store"] is False
    assert "tools" not in request


@pytest.mark.asyncio
async def test_active_runtime_skips_classifier_when_physical_or_local_score_is_already_smart():
    primary = client(response("응."))
    classifier_client = client(response(classification(level="low")))
    llm = LLM(settings(), client=primary, classifier_client=classifier_client)
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        result = await llm.answer(
            store,
            Scope(None, 10, 100),
            "사용자",
            "이 알고리즘의 시간 복잡도를 증명해 줘",
        )
        assert result == "응."
        assert primary.responses.create.await_args.kwargs["model"] == "smart"
        classifier_client.responses.create.assert_not_awaited()
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_active_runtime_uses_classifier_result_for_answer_model(tmp_path):
    primary = client(response("응."))
    classifier_client = client(response(classification(level="high")))
    log_path = tmp_path / "usage.jsonl"
    llm = LLM(
        settings(usage_log_path=str(log_path)),
        client=primary,
        classifier_client=classifier_client,
    )
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        secret_request = "P=NP라고 가정하면 polynomial hierarchy에는 무슨 일이 생겨?"
        result = await llm.answer(
            store,
            Scope(None, 10, 100),
            "사용자",
            secret_request,
        )
        assert result == "응."
        assert classifier_client.responses.create.await_count == 1
        assert primary.responses.create.await_args.kwargs["model"] == "smart"
        rows = [json.loads(line) for line in log_path.read_text().splitlines()]
        answer_row = next(row for row in rows if row["operation"] == "answer")
        assert answer_row["semantic_route_status"] == "completed"
        assert answer_row["semantic_route_level"] == "high"
        assert answer_row["model_route_policy"] == "chat-hybrid-v4"
        assert secret_request not in log_path.read_text()
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_invalid_classifier_output_does_not_block_answer():
    primary = client(response("응."))
    classifier_client = client(response("I think this is difficult."))
    llm = LLM(settings(), client=primary, classifier_client=classifier_client)
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        result = await llm.answer(
            store, Scope(None, 10, 100), "사용자", "이건 어떻게 생각해?"
        )
        assert result == "응."
        assert primary.responses.create.await_args.kwargs["model"] == "fast"
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_shadow_runtime_does_not_block_or_change_actual_route(tmp_path):
    release = asyncio.Event()

    async def delayed_classifier(**kwargs):
        await release.wait()
        return response(classification(level="high"))

    primary = client(response("응."))
    classifier_client = client(response("unused"))
    classifier_client.responses.create.side_effect = delayed_classifier
    log_path = tmp_path / "usage.jsonl"
    config = settings(
        routing_classifier_mode="shadow",
        usage_log_path=str(log_path),
    )
    llm = LLM(config, client=primary, classifier_client=classifier_client)
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        result = await asyncio.wait_for(
            llm.answer(store, Scope(None, 10, 100), "사용자", "이건 어떻게 생각해?"),
            timeout=0.5,
        )
        assert result == "응."
        assert primary.responses.create.await_args.kwargs["model"] == "fast"
        await asyncio.sleep(0)
        assert classifier_client.responses.create.await_count == 1

        release.set()
        await llm.close()
        rows = [json.loads(line) for line in log_path.read_text().splitlines()]
        shadow = next(row for row in rows if row["operation"] == "model_route_shadow")
        assert shadow["model_route_baseline_tier"] == "fast"
        assert shadow["model_route_proposed_tier"] == "smart"
        assert shadow["semantic_route_level"] == "high"
    finally:
        if not primary.close.await_count:
            release.set()
            await llm.close()
        store.close()
