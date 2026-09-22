import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.reference_gated_recall import (
    detect_explicit_self_reference,
    plan_reference_gated_recall,
)
from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def _response():
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "test-model",
        "output": [{
            "type": "message",
            "id": "msg_test",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "응", "annotations": []}],
        }],
    }


def _client(calls):
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    return AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _reference(call):
    return json.loads(call["input"][0]["content"].split("\n", 1)[1])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("전에 말했던 면접 있잖아", "past_reference"),
        ("저번에 얘기했던 삼성 면접 말인데", "past_reference"),
        ("DM에서 말한 면접 기억나?", "dm_reference"),
        ("내가 말했던 그 면접 있잖아", "self_reference"),
        ("그거 기억나?", "recall_question"),
        ("내가 전에 말한 거 기억나?", "self_reference"),
    ],
)
def test_explicit_reference_detector_accepts_only_strong_recall_forms(text, expected):
    assert detect_explicit_self_reference(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "면접 망했다",
        "고양이 얘기인데",
        "철수가 전에 말했던 면접 기억나?",
        "내가 면접 망했다고 말했어",
        "오늘 면접이 있다는 얘기야",
    ],
)
def test_explicit_reference_detector_rejects_topical_or_third_party_mentions(text):
    assert detect_explicit_self_reference(text) == ""


def test_unique_single_anchor_can_authorize_one_cross_space_memory():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        interview_id = store.add_memory_item(
            dm,
            "다음 주 삼성 면접이 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        store.add_memory_item(
            dm,
            "고양이 이름은 하루다",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        plan = plan_reference_gated_recall(
            store,
            server,
            "전에 말했던 면접 있잖아",
            use_memory=True,
            allow_cross_space=True,
        )

        assert plan.status == "authorized"
        assert plan.candidate_count == 2
        assert plan.relevant_count == 1
        assert [item.id for item in plan.selected] == [interview_id]
        assert plan.authorization_reason == "owner_explicit_reference"
    finally:
        store.close()


def test_ambiguous_single_anchor_does_not_guess_between_memories():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        for content in ("삼성 면접이 다음 주다", "두산 면접이 다음 달이다"):
            store.add_memory_item(
                dm,
                content,
                kind=MemoryKind.EVENT,
                disclosure=MemoryDisclosure.REFERENCE_GATED,
            )

        plan = plan_reference_gated_recall(
            store,
            server,
            "전에 말했던 면접 있잖아",
            use_memory=True,
            allow_cross_space=True,
        )

        assert plan.status == "ambiguous_single_anchor"
        assert plan.relevant_count == 2
        assert plan.selected == ()
    finally:
        store.close()


def test_multiple_topic_anchors_select_only_best_matching_memory():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        samsung_id = store.add_memory_item(
            dm,
            "삼성 면접이 다음 주에 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        store.add_memory_item(
            dm,
            "두산 면접이 다음 달에 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        plan = plan_reference_gated_recall(
            store,
            server,
            "내가 전에 말했던 삼성 면접 있잖아",
            use_memory=True,
            allow_cross_space=True,
        )

        assert plan.status == "authorized"
        assert [item.id for item in plan.selected] == [samsung_id]
    finally:
        store.close()


def test_current_channel_only_and_owner_dm_never_open_cross_space_gate():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        store.add_memory_item(
            dm,
            "삼성 면접이 다음 주에 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        current_only = plan_reference_gated_recall(
            store,
            server,
            "전에 말했던 삼성 면접",
            use_memory=True,
            allow_cross_space=False,
        )
        owner_dm = plan_reference_gated_recall(
            store,
            dm,
            "전에 말했던 삼성 면접",
            use_memory=True,
            allow_cross_space=True,
        )

        assert current_only.status == "current_channel_only"
        assert current_only.selected == ()
        assert owner_dm.status == "owner_dm"
        assert owner_dm.selected == ()
    finally:
        store.close()


def test_other_speaker_cannot_unlock_another_users_memory():
    store = Store(":memory:")
    owner_dm = Scope(None, 10, 100)
    other_server = Scope(1, 20, 200, True)
    try:
        store.add_memory_item(
            owner_dm,
            "삼성 면접이 다음 주에 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        plan = plan_reference_gated_recall(
            store,
            other_server,
            "내가 전에 말했던 삼성 면접 기억나?",
            use_memory=True,
            allow_cross_space=True,
        )

        assert plan.detected is True
        assert plan.status == "no_candidates"
        assert plan.selected == ()
    finally:
        store.close()


def test_same_space_reference_gated_memory_is_not_cross_space_recall_candidate():
    store = Store(":memory:")
    source = Scope(1, 20, 100, True)
    same_guild = Scope(1, 30, 100, True)
    try:
        store.add_memory_item(
            source,
            "삼성 면접이 다음 주에 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        plan = plan_reference_gated_recall(
            store,
            same_guild,
            "전에 말했던 삼성 면접 있잖아",
            use_memory=True,
            allow_cross_space=True,
        )

        assert plan.status == "no_candidates"
        assert plan.selected == ()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_explicit_owner_reference_injects_only_related_authorized_memory():
    calls = []
    llm = LLM(
        Settings(
            "test",
            "test",
            model="test-model",
            usage_log_path="",
            chat_web_search=False,
            external_context_policy="full",
        ),
        client=_client(calls),
    )
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        interview_id = store.add_memory_item(
            dm,
            "다음 주 삼성 면접이 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        store.add_memory_item(
            dm,
            "고양이 이름은 하루다",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        await llm.answer(
            store,
            server,
            "사용자",
            "내가 전에 말했던 삼성 면접 있잖아",
        )

        reference = _reference(calls[-1])
        assert reference["authorized_factual_memory"] == [{
            "kind": "event",
            "content": "다음 주 삼성 면접이 있다",
            "confidence": 1.0,
            "authorization": "owner_explicit_reference",
        }]
        assert "고양이 이름은 하루다" not in json.dumps(reference, ensure_ascii=False)
        assert "authorized_factual_memory" in calls[-1]["instructions"]

        provenance = CURRENT_CONTEXT_PROVENANCE.get()
        assert provenance["factual_recall"]["status"] == "authorized"
        assert provenance["factual_recall"]["selected_item_ids"] == [interview_id]
        admitted = [
            row
            for row in provenance["structured_memory"]
            if row.get("projection") == "authorized_factual_recall"
        ]
        assert [row["item_id"] for row in admitted] == [interview_id]
    finally:
        CURRENT_CONTEXT_PROVENANCE.set(None)
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_topic_without_explicit_reference_does_not_inject_cross_space_fact():
    calls = []
    llm = LLM(
        Settings(
            "test",
            "test",
            model="test-model",
            usage_log_path="",
            chat_web_search=False,
            external_context_policy="full",
        ),
        client=_client(calls),
    )
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        store.add_memory_item(
            dm,
            "다음 주 삼성 면접이 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        await llm.answer(store, server, "사용자", "삼성 면접 망했다")

        reference = _reference(calls[-1])
        assert reference["authorized_factual_memory"] == []
        assert "다음 주 삼성 면접이 있다" not in json.dumps(reference, ensure_ascii=False)
        assert CURRENT_CONTEXT_PROVENANCE.get()["factual_recall"]["status"] == (
            "no_explicit_reference"
        )
    finally:
        CURRENT_CONTEXT_PROVENANCE.set(None)
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_current_channel_only_request_suppresses_reference_gated_recall():
    calls = []
    llm = LLM(
        Settings(
            "test",
            "test",
            model="test-model",
            usage_log_path="",
            chat_web_search=False,
            external_context_policy="full",
        ),
        client=_client(calls),
    )
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        store.add_memory_item(
            dm,
            "다음 주 삼성 면접이 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        await llm.answer(
            store,
            server,
            "사용자",
            "이 채널에서 전에 말했던 삼성 면접 얘기만 봐줘",
        )

        reference = _reference(calls[-1])
        assert reference["authorized_factual_memory"] == []
        assert "다음 주 삼성 면접이 있다" not in json.dumps(reference, ensure_ascii=False)
        assert CURRENT_CONTEXT_PROVENANCE.get()["factual_recall"]["status"] == (
            "current_channel_only"
        )
    finally:
        CURRENT_CONTEXT_PROVENANCE.set(None)
        await llm.close()
        store.close()
