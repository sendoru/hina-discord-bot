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
    ClassificationOutcome,
    InvalidClassifierResponse,
    SemanticClassification,
    SemanticModelRouter,
    apply_classification,
    parse_classification,
    prepare_hybrid_plan,
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
    )


def response(text, *, status="completed"):
    return NS(status=status, output_text=text, output=[], usage=None)


def client(result):
    return NS(
        provider_name="gemini",
        responses=NS(create=AsyncMock(return_value=result)),
        close=AsyncMock(),
    )


def test_classifier_parser_accepts_only_bounded_schema():
    parsed = parse_classification(
        '{"level":"high","codes":["debugging"],"uncertain":false}'
    )
    assert parsed == SemanticClassification("high", ("debugging",), False)

    fenced = parse_classification(
        '```json\n{"level":"low","codes":["simple_chat"],"uncertain":false}\n```'
    )
    assert fenced.level == "low"

    for invalid in (
        "not-json",
        '{"level":"critical","codes":["debugging"],"uncertain":false}',
        '{"level":"high","codes":["answer_the_user"],"uncertain":false}',
        '{"level":"high","codes":["debugging"],"uncertain":false,"reason":"secret"}',
    ):
        with pytest.raises(InvalidClassifierResponse):
            parse_classification(invalid)


def test_hybrid_matrix_combines_semantics_with_objective_axes():
    config = settings()
    routine = build_model_plan(config, information("안녕"))
    prepared, bypass = prepare_hybrid_plan(config, routine, "active")
    assert not bypass

    high = apply_classification(
        config,
        prepared,
        ClassificationOutcome(
            "completed", SemanticClassification("high", ("proof_or_derivation",), False)
        ),
    )
    assert high.tier == ModelTier.SMART
    assert high.model_route_decision_source == "semantic"

    medium = apply_classification(
        config,
        prepared,
        ClassificationOutcome(
            "completed", SemanticClassification("medium", ("multi_step_reasoning",), False)
        ),
    )
    assert medium.tier == ModelTier.FAST

    loaded = build_model_plan(config, information("x" * 1300))
    loaded_prepared, bypass = prepare_hybrid_plan(config, loaded, "active")
    assert not bypass
    combined = apply_classification(
        config,
        loaded_prepared,
        ClassificationOutcome(
            "completed", SemanticClassification("medium", ("multi_step_reasoning",), False)
        ),
    )
    assert combined.tier == ModelTier.SMART
    assert combined.model_route_decision_source == "combined"


def test_uncertain_or_failed_classifier_uses_exact_baseline_tier():
    config = settings()
    baseline = build_model_plan(config, information("안녕"))
    prepared, _ = prepare_hybrid_plan(config, baseline, "active")

    uncertain = apply_classification(
        config,
        prepared,
        ClassificationOutcome(
            "completed", SemanticClassification("high", ("ambiguous",), True)
        ),
    )
    failed = apply_classification(config, prepared, ClassificationOutcome("timeout"))

    assert uncertain.tier == baseline.tier
    assert uncertain.semantic_route_status == "uncertain"
    assert failed.tier == baseline.tier
    assert failed.semantic_route_status == "timeout"
    assert failed.model_route_decision_source == "rules_fallback"


@pytest.mark.asyncio
async def test_classifier_input_withholds_unowned_anchor_and_uses_only_owned_prior_request():
    config = settings()
    classifier_client = client(response(
        '{"level":"medium","codes":["constraint_interaction"],"uncertain":false}'
    ))
    usage = UsageLogger("")
    router = SemanticModelRouter(config, classifier_client, usage)
    info = information(
        "그 조건까지 고려하면?",
        prior="내가 앞서 요청한 내용",
        anchor="third-party-secret",
    )
    baseline = build_model_plan(config, info)
    prepared, _ = prepare_hybrid_plan(config, baseline, "active")

    outcome = await router.classify(info, prepared)

    assert outcome.status == "completed"
    request = classifier_client.responses.create.await_args.kwargs
    payload = json.loads(request["input"])
    assert payload["current_request"] == "그 조건까지 고려하면?"
    assert payload["prior_user_request"] == "내가 앞서 요청한 내용"
    assert payload["anchor"] == {"source": "external_reply", "text": ""}
    assert payload["context_signals"] == {
        "anchor_present": True,
        "anchor_included": False,
        "reference_count": 0,
        "web_search_required": False,
    }
    assert "third-party-secret" not in request["input"]
    assert set(payload) == {
        "current_request",
        "prior_user_request",
        "anchor",
        "context_signals",
        "objective_load",
    }
    assert request["store"] is False
    assert "tools" not in request


@pytest.mark.asyncio
async def test_objective_high_and_high_precision_rules_skip_classifier():
    config = settings()
    classifier_client = client(response("unused"))
    router = SemanticModelRouter(config, classifier_client, UsageLogger(""))

    long_info = information("x" * 4000)
    long_plan = await router.active_plan(
        long_info, build_model_plan(config, long_info)
    )
    rule_info = information("이 알고리즘을 증명해 줘")
    rule_plan = await router.active_plan(
        rule_info, build_model_plan(config, rule_info)
    )

    assert long_plan.tier == ModelTier.SMART
    assert long_plan.semantic_route_status == "skipped_objective_high"
    assert rule_plan.tier == ModelTier.SMART
    assert rule_plan.semantic_route_status == "skipped_rule_high"
    classifier_client.responses.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_runtime_uses_classifier_result_for_answer_model(tmp_path):
    primary = client(response("응."))
    classifier_client = client(response(
        '{"level":"high","codes":["proof_or_derivation"],"uncertain":false}'
    ))
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
        classify_row = next(row for row in rows if row["operation"] == "model_route_classify")
        answer_row = next(row for row in rows if row["operation"] == "answer")
        assert classify_row["routing_classifier_provider"] == "gemini"
        assert answer_row["semantic_route_status"] == "completed"
        assert answer_row["semantic_route_level"] == "high"
        assert answer_row["model_route_decision_source"] == "semantic"
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
        return response(
            '{"level":"high","codes":["proof_or_derivation"],"uncertain":false}'
        )

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
