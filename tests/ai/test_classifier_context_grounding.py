import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.freshness import FreshnessMode
from hina_bot.ai.information_plan import InformationPlan
from hina_bot.ai.information_routing import InformationRoute
from hina_bot.ai.model_routing import build_model_plan
from hina_bot.ai.routing_plan import RoutingPlan, build_routing_plan
from hina_bot.ai.rp_output_policy import ProvenanceMode
from hina_bot.ai.semantic_model_routing import SemanticModelRouter, prepare_hybrid_plan
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def settings():
    return Settings(
        api_key="primary-key",
        discord_token="token",
        provider="gemini",
        model="fixed",
        model_routing_mode="adaptive",
        fast_model="fast",
        smart_model="smart",
        routing_classifier_mode="active",
        routing_classifier_provider="gemini",
        routing_classifier_model="classifier",
        routing_classifier_api_key="classifier-key",
        usage_log_path="",
        chat_web_search=False,
    )


def response(text):
    return NS(status="completed", output_text=text, output=[], usage=None)


def client(result):
    return NS(
        provider_name="gemini",
        responses=NS(create=AsyncMock(return_value=result)),
        close=AsyncMock(),
    )


def information(routing, *, references=(), search_mode="none"):
    return InformationPlan(
        routing=routing,
        route=InformationRoute.GENERAL,
        references=references,
        freshness=FreshnessMode.STATIC,
        fact_question=False,
        search_mode=search_mode,
        provenance=ProvenanceMode.SILENT,
    )


def test_routing_plan_exposes_only_safe_explicit_reply_to_classifier():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    try:
        assistant = build_routing_plan(
            store,
            scope,
            "왜?",
            [{
                "context_kind": "replied_message",
                "content": "앞에서 설명한 복잡한 근거",
                "role": "assistant",
            }],
            use_memory=False,
        )
        own = build_routing_plan(
            store,
            scope,
            "왜?",
            [{
                "context_kind": "replied_message",
                "content": "내가 직접 쓴 복잡한 요청",
                "role": "user",
                "author_user_id": "100",
            }],
            use_memory=False,
        )
        third_party = build_routing_plan(
            store,
            scope,
            "왜?",
            [
                {
                    "context_kind": "speaker_thread",
                    "content": "내 이전 요청",
                    "role": "user",
                    "author_user_id": "100",
                },
                {
                    "context_kind": "replied_message",
                    "content": "third-party-secret",
                    "role": "user",
                    "author_user_id": "999",
                },
            ],
            use_memory=False,
        )

        assert assistant.classifier_anchor == "앞에서 설명한 복잡한 근거"
        assert own.classifier_anchor == "내가 직접 쓴 복잡한 요청"
        assert third_party.anchor == "third-party-secret"
        assert third_party.classifier_anchor == ""
        assert third_party.prior_user_request == ""
    finally:
        store.close()


@pytest.mark.asyncio
async def test_classifier_payload_includes_bounded_anchor_and_context_signals():
    config = settings()
    classifier_client = client(response(
        '{"level":"medium","codes":["constraint_interaction"],"uncertain":false}'
    ))
    router = SemanticModelRouter(config, classifier_client, UsageLogger(""))
    routing = RoutingPlan(
        "그 부분은 왜?",
        "그 부분은 왜?",
        anchor="앞에서 설명한 복잡한 근거",
        anchor_source="explicit_reply",
        prior_user_request="원래 요청",
        classifier_anchor="앞에서 설명한 복잡한 근거",
    )
    info = information(routing, references=("r1", "r2"), search_mode="required")
    baseline = build_model_plan(config, info)
    prepared, _ = prepare_hybrid_plan(config, baseline, "active")

    outcome = await router.classify(info, prepared)

    assert outcome.status == "completed"
    request = classifier_client.responses.create.await_args.kwargs
    payload = json.loads(request["input"])
    assert payload["anchor"] == {
        "source": "explicit_reply",
        "text": "앞에서 설명한 복잡한 근거",
    }
    assert payload["context_signals"] == {
        "anchor_present": True,
        "anchor_included": True,
        "reference_count": 2,
        "web_search_required": True,
    }
    assert set(payload) == {
        "current_request",
        "prior_user_request",
        "anchor",
        "context_signals",
        "objective_load",
    }


@pytest.mark.asyncio
async def test_classifier_payload_marks_withheld_third_party_anchor_without_forwarding_it():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    config = settings()
    classifier_client = client(response(
        '{"level":"low","codes":["ambiguous"],"uncertain":true}'
    ))
    router = SemanticModelRouter(config, classifier_client, UsageLogger(""))
    try:
        routing = build_routing_plan(
            store,
            scope,
            "왜?",
            [{
                "context_kind": "replied_message",
                "content": "third-party-secret",
                "role": "user",
                "author_user_id": "999",
            }],
            use_memory=False,
        )
        info = information(routing)
        baseline = build_model_plan(config, info)
        prepared, _ = prepare_hybrid_plan(config, baseline, "active")

        outcome = await router.classify(info, prepared)

        assert outcome.status == "completed"
        request = classifier_client.responses.create.await_args.kwargs
        payload = json.loads(request["input"])
        assert payload["anchor"] == {"source": "explicit_reply", "text": ""}
        assert payload["context_signals"]["anchor_present"] is True
        assert payload["context_signals"]["anchor_included"] is False
        assert "third-party-secret" not in request["input"]
    finally:
        store.close()
