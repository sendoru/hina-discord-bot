import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.freshness import FreshnessMode
from hina_bot.ai.information_plan import InformationPlan
from hina_bot.ai.information_routing import InformationRoute
from hina_bot.ai.routing_plan import RoutingPlan, build_routing_plan
from hina_bot.ai.rp_output_policy import ProvenanceMode
from hina_bot.ai.semantic_model_routing import SemanticModelRouter
from hina_bot.ai.usage import UsageLogger
from hina_bot.ai.vision import VisualInput
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


def classification(*, level="medium", uncertain=False):
    return json.dumps({
        "level": level,
        "codes": ["constraint_interaction" if level != "low" else "ambiguous"],
        "uncertain": uncertain,
        "web_need": "none",
        "web_codes": ["stable_or_contextual"],
        "web_uncertain": False,
    })


def information(routing, *, references=(), search_mode="none"):
    return InformationPlan(
        routing=routing,
        route=InformationRoute.GENERAL,
        references=references,
        freshness=FreshnessMode.STATIC,
        fact_question=False,
        search_mode=search_mode,
        provenance=ProvenanceMode.SILENT,
        search_locked=True,
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
        assert all(item.text != "third-party-secret" for item in third_party.classifier_context)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_classifier_payload_includes_bounded_anchor_and_context_signals():
    config = settings()
    classifier_client = client(response(classification()))
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

    outcome = await router.classify(info, baseline_tier="fast")

    assert outcome.status == "completed"
    request = classifier_client.responses.create.await_args.kwargs
    payload = json.loads(request["input"])
    assert payload["anchor"] == {
        "source": "explicit_reply",
        "text": "앞에서 설명한 복잡한 근거",
    }
    assert payload["routing_context"] == []
    assert payload["context_signals"] == {
        "anchor_present": True,
        "anchor_included": True,
        "routing_context_count": 0,
        "cross_speaker_context_count": 0,
        "cross_speaker_context_chars": 0,
        "reference_count": 2,
        "web_search_required": True,
        "web_search_locked": True,
    }
    assert set(payload) == {
        "current_request",
        "prior_user_request",
        "anchor",
        "routing_context",
        "context_signals",
    }


@pytest.mark.asyncio
async def test_classifier_receives_task_linked_visuals_but_not_ambient_visuals():
    config = settings()
    classifier_client = client(response(classification(level="high")))
    router = SemanticModelRouter(config, classifier_client, UsageLogger(""))
    routing = RoutingPlan(
        "자 여깄어",
        "자 여깄어",
        prior_user_request="히나야 사과게임 풀어줘",
    )
    visuals = (
        VisualInput(
            data=b"\x89PNG\r\n\x1a\ncurrent",
            mime_type="image/png",
            source="attachment",
            context_kind="current_message",
            reference_strength="current_message",
        ),
        VisualInput(
            data=b"\x89PNG\r\n\x1a\nthread",
            mime_type="image/png",
            source="attachment",
            context_kind="speaker_thread",
            reference_strength="same_speaker",
        ),
        VisualInput(
            data=b"\x89PNG\r\n\x1a\nambient",
            mime_type="image/png",
            source="attachment",
            context_kind="channel_ambient",
            reference_strength="ambient",
        ),
    )

    outcome = await router.classify(
        information(routing),
        baseline_tier="fast",
        visual_inputs=visuals,
    )

    assert outcome.status == "completed"
    request = classifier_client.responses.create.await_args.kwargs
    assert isinstance(request["input"], list)
    content = request["input"][0]["content"]
    payload = json.loads(content[0]["text"])
    assert payload["current_request"] == "자 여깄어"
    assert payload["prior_user_request"] == "히나야 사과게임 풀어줘"

    image_blocks = [block for block in content if block["type"] == "input_image"]
    assert len(image_blocks) == 2
    assert all(block["image_url"].startswith("data:image/png;base64,") for block in image_blocks)
    metadata_blocks = [
        json.loads(block["text"].removeprefix("Untrusted visual evidence metadata(JSON):"))
        for block in content
        if block["type"] == "input_text"
        and block["text"].startswith("Untrusted visual evidence metadata(JSON):")
    ]
    assert metadata_blocks == [
        {
            "index": 1,
            "context_kind": "current_message",
            "reference_strength": "current_message",
        },
        {
            "index": 2,
            "context_kind": "speaker_thread",
            "reference_strength": "same_speaker",
        },
    ]


@pytest.mark.asyncio
async def test_classifier_no_visual_request_keeps_legacy_string_input():
    config = settings()
    classifier_client = client(response(classification()))
    router = SemanticModelRouter(config, classifier_client, UsageLogger(""))
    routing = RoutingPlan("안녕", "안녕")

    await router.classify(information(routing), baseline_tier="fast")

    request = classifier_client.responses.create.await_args.kwargs
    assert isinstance(request["input"], str)
    assert json.loads(request["input"])["current_request"] == "안녕"


@pytest.mark.asyncio
async def test_classifier_payload_marks_withheld_third_party_anchor_without_forwarding_it():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    config = settings()
    classifier_client = client(response(classification(level="low", uncertain=True)))
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

        outcome = await router.classify(info, baseline_tier="fast")

        assert outcome.status == "completed"
        request = classifier_client.responses.create.await_args.kwargs
        payload = json.loads(request["input"])
        assert payload["anchor"] == {"source": "explicit_reply", "text": ""}
        assert payload["context_signals"]["anchor_present"] is True
        assert payload["context_signals"]["anchor_included"] is False
        assert payload["routing_context"] == []
        assert "third-party-secret" not in request["input"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_context_reference_samples_recent_cross_speaker_bot_exchange():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    config = settings()
    classifier_client = client(response(classification()))
    router = SemanticModelRouter(config, classifier_client, UsageLogger(""))
    rows = [
        {
            "message_id": "10",
            "context_kind": "channel_ambient",
            "content": "히나야 그 때 자동사인지 타동사인지는 어떻게 구별해",
            "role": "user",
            "author_user_id": "200",
            "direct_trigger": True,
        },
        {
            "message_id": "11",
            "context_kind": "channel_ambient",
            "content": "목적어를 곧바로 데리고 오는지 보면 돼.",
            "role": "assistant",
            "reply_target_user_id": "200",
        },
        {
            "message_id": "12",
            "context_kind": "channel_ambient",
            "content": "이건 그냥 채널에서 한 일반 잡담",
            "role": "user",
            "author_user_id": "300",
            "direct_trigger": False,
        },
    ]
    current = (
        "히나야 위에 얘기 연장선인데 목적의 의미를 가지는 to부정사가 오는 경우에 "
        "이게 전치사가 붙은 부사절인지 목적어인지는 어떻게 구분해야 돼"
    )
    try:
        routing = build_routing_plan(
            store,
            scope,
            current,
            rows,
            use_memory=False,
            classifier_context_policy="full",
        )
        assert routing.routing_query == current
        cross = [
            item
            for item in routing.classifier_context
            if item.kind == "cross_speaker_bot_interaction"
        ]
        assert [item.text for item in cross] == [
            "히나야 그 때 자동사인지 타동사인지는 어떻게 구별해",
            "목적어를 곧바로 데리고 오는지 보면 돼.",
        ]
        assert [item.ownership for item in cross] == ["external", "assistant"]
        assert all("일반 잡담" not in item.text for item in routing.classifier_context)
        assert sum(len(item.text) for item in cross) <= 1200

        outcome = await router.classify(information(routing), baseline_tier="fast")
        assert outcome.status == "completed"
        request = classifier_client.responses.create.await_args.kwargs
        payload = json.loads(request["input"])
        assert payload["context_signals"]["cross_speaker_context_count"] == 2
        assert payload["context_signals"]["cross_speaker_context_chars"] == sum(
            len(item.text) for item in cross
        )
    finally:
        store.close()


def test_self_contained_question_does_not_sample_cross_speaker_context():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    rows = [
        {
            "message_id": "10",
            "context_kind": "channel_ambient",
            "content": "히나야 그 때 자동사인지 타동사인지는 어떻게 구별해",
            "role": "user",
            "author_user_id": "200",
            "direct_trigger": True,
        },
        {
            "message_id": "11",
            "context_kind": "channel_ambient",
            "content": "목적어를 곧바로 데리고 오는지 보면 돼.",
            "role": "assistant",
            "reply_target_user_id": "200",
        },
    ]
    try:
        routing = build_routing_plan(
            store,
            scope,
            "히나야 C++에서 virtual 함수가 뭐야?",
            rows,
            use_memory=False,
            classifier_context_policy="full",
        )
        assert all(
            item.kind != "cross_speaker_bot_interaction"
            for item in routing.classifier_context
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_short_nonquestion_reference_gets_recent_thread_and_explicit_reply_context():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    config = settings()
    classifier_client = client(response(classification(level="high")))
    router = SemanticModelRouter(config, classifier_client, UsageLogger(""))
    source = (
        "이 음식점은 정상적으로 밥을 팔아서 돈을 버는 본업이 있지만, "
        "옆의 도박장에서 빚을 지고 정부가 채무를 탕감해 주는 구조다. "
        "투자자는 음식점 대신 도박장만 증축하고 정부는 손실을 반복해서 구제한다."
    )
    rows = [
        {
            "context_kind": "speaker_thread",
            "content": "아까 그 도박장 글과 비슷한 정부실패 사례가 뭐가 있을까",
            "role": "user",
            "author_user_id": "100",
        },
        {
            "context_kind": "speaker_thread",
            "content": "도박 규제와 구제정책의 인센티브 문제를 생각해볼 수 있어.",
            "role": "assistant",
            "reply_target_user_id": "100",
        },
        {
            "context_kind": "speaker_thread",
            "content": "재부팅해서 날아갔나",
            "role": "user",
            "author_user_id": "100",
        },
        {
            "context_kind": "speaker_thread",
            "content": "응? 무슨 일이야?",
            "role": "assistant",
            "reply_target_user_id": "100",
        },
        {
            "context_kind": "replied_message",
            "content": source,
            "role": "user",
            "author_user_id": "999",
        },
    ]
    try:
        routing = build_routing_plan(
            store,
            scope,
            "ㅇㅇ 이거",
            rows,
            use_memory=False,
            classifier_context_policy="full",
        )
        assert routing.anchor == ""  # This short acknowledgement is not the old question-shaped follow-up.
        assert routing.prior_user_request == ""

        info = information(routing)
        outcome = await router.classify(info, baseline_tier="fast")

        assert outcome.status == "completed"
        request = classifier_client.responses.create.await_args.kwargs
        payload = json.loads(request["input"])
        context = payload["routing_context"]
        assert context[-1] == {
            "kind": "replied_message",
            "role": "user",
            "ownership": "external",
            "text": source,
        }
        assert any(
            item["ownership"] == "self"
            and "정부실패 사례" in item["text"]
            for item in context
        )
        assert any(
            item["ownership"] == "assistant"
            and "무슨 일이야" in item["text"]
            for item in context
        )
        assert sum(len(item["text"]) for item in context) <= 4000
        assert payload["context_signals"]["routing_context_count"] == len(context)
    finally:
        store.close()
