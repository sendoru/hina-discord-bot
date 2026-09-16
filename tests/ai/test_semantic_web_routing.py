import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.freshness import FreshnessMode
from hina_bot.ai.information_evidence import SearchDecision, search_decision
from hina_bot.ai.information_routing import (
    InformationRoute,
    classify_information_request,
)
from hina_bot.ai.runtime_llm import LLM
from hina_bot.ai.semantic_model_routing import parse_classification
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
        "chat_web_search": True,
    }
    values.update(overrides)
    return Settings(**values)


def response(text, *, status="completed"):
    return NS(status=status, output_text=text, output=[], usage=None)


def client(result):
    return NS(
        provider_name="gemini",
        responses=NS(create=AsyncMock(return_value=result)),
        close=AsyncMock(),
    )


def classification(*, level="low", web_need="none", web_code="stable_or_contextual"):
    return json.dumps({
        "level": level,
        "codes": ["simple_chat" if level == "low" else "multi_step_reasoning"],
        "uncertain": False,
        "web_need": web_need,
        "web_codes": [web_code],
        "web_uncertain": False,
    })


def test_combined_classifier_parser_accepts_bounded_web_schema():
    parsed = parse_classification(classification(
        level="medium",
        web_need="required",
        web_code="version_specific",
    ))
    assert parsed.level == "medium"
    assert parsed.web_need == "required"
    assert parsed.web_codes == ("version_specific",)
    assert parsed.web_uncertain is False


def test_search_decision_locks_high_confidence_rules_but_opens_general_queries():
    source = classify_information_request("출처 알려줘")
    assert search_decision(source, [], enabled=True) == SearchDecision(
        "required", True, "explicit_source"
    )

    clock = classify_information_request("지금 몇 시야?")
    assert clock.route == InformationRoute.CLOCK
    assert search_decision(clock, [], enabled=True) == SearchDecision(
        "none", True, "local_only"
    )

    static = classify_information_request("--experimental-strip-types는 experimental이야?")
    assert static.freshness == FreshnessMode.STATIC
    assert search_decision(static, [], enabled=True) == SearchDecision(
        "none", False, "semantic_open"
    )

    temporal = classify_information_request("오늘 C++ coroutine은 어떻게 동작해?")
    assert temporal.freshness == FreshnessMode.AUTO
    assert search_decision(temporal, [], enabled=True) == SearchDecision(
        "auto", False, "semantic_temporal"
    )


@pytest.mark.asyncio
async def test_active_classifier_promotes_unmarked_version_question_to_required_web():
    primary = client(response("응."))
    classifier_client = client(response(classification(
        web_need="required",
        web_code="version_specific",
    )))
    llm = LLM(settings(), client=primary, classifier_client=classifier_client)
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        result = await llm.answer(
            store,
            Scope(None, 10, 100),
            "사용자",
            "Node.js의 --experimental-strip-types는 experimental이야?",
        )
        assert result == "응."
        request = primary.responses.create.await_args.kwargs
        assert request["tool_choice"] == "required"
        assert request["tools"][0]["type"] == "web_search"
        assert classifier_client.responses.create.await_count == 1
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_active_classifier_can_demote_soft_temporal_auto_to_none():
    primary = client(response("응."))
    classifier_client = client(response(classification(
        web_need="none",
        web_code="stable_or_contextual",
    )))
    llm = LLM(settings(), client=primary, classifier_client=classifier_client)
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        await llm.answer(
            store,
            Scope(None, 10, 100),
            "사용자",
            "오늘 C++ coroutine은 어떻게 동작해?",
        )
        request = primary.responses.create.await_args.kwargs
        assert "tools" not in request
        assert "tool_choice" not in request
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_hard_required_web_cannot_be_demoted_by_classifier():
    primary = client(response("응."))
    classifier_client = client(response(classification(
        web_need="none",
        web_code="stable_or_contextual",
    )))
    llm = LLM(
        settings(runtime_default_location="서울"),
        client=primary,
        classifier_client=classifier_client,
    )
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        await llm.answer(
            store,
            Scope(None, 10, 100),
            "사용자",
            "오늘 서울 날씨 어때?",
        )
        request = primary.responses.create.await_args.kwargs
        assert request["tool_choice"] == "required"
        assert request["tools"][0]["type"] == "web_search"
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_shadow_classifier_records_web_proposal_without_changing_actual_request(tmp_path):
    primary = client(response("응."))
    classifier_client = client(response(classification(
        web_need="required",
        web_code="version_specific",
    )))
    log_path = tmp_path / "usage.jsonl"
    llm = LLM(
        settings(
            routing_classifier_mode="shadow",
            usage_log_path=str(log_path),
        ),
        client=primary,
        classifier_client=classifier_client,
    )
    llm.lore = LoreIndex([])
    store = Store(":memory:")
    try:
        await llm.answer(
            store,
            Scope(None, 10, 100),
            "사용자",
            "Node.js의 --experimental-strip-types는 experimental이야?",
        )
        request = primary.responses.create.await_args.kwargs
        assert "tools" not in request
        assert "tool_choice" not in request

        await llm.close()
        rows = [json.loads(line) for line in log_path.read_text().splitlines()]
        shadow = next(row for row in rows if row["operation"] == "model_route_shadow")
        assert shadow["search_route_baseline_mode"] == "none"
        assert shadow["search_route_proposed_mode"] == "required"
        assert shadow["semantic_web_need"] == "required"
        assert shadow["semantic_web_codes"] == ["version_specific"]
    finally:
        if not primary.close.await_count:
            await llm.close()
        store.close()
