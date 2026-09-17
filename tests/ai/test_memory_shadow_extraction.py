import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_extraction import (
    ExtractedMemoryItem,
    build_shadow_turns,
    parse_shadow_extraction,
    persist_shadow_items,
)
from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def _settings():
    return NS(
        summary_every=2,
        model="fixed-model",
        model_routing_mode="fixed",
        memory_routing_smart_threshold=2.0,
        fast_model="fast-model",
        smart_model="smart-model",
        memory_output_tokens=4096,
        gemini_thinking_level="low",
        gemini_fast_thinking_level="minimal",
        gemini_smart_thinking_level="medium",
        provider="openai",
    )


class Harness(MemorySummaryMixin):
    pass


def _harness(*responses):
    harness = Harness()
    harness.settings = _settings()
    harness.client = object()
    harness.usage = NS(
        request=AsyncMock(side_effect=responses),
        routing_event=lambda *args, **kwargs: None,
    )
    return harness


def _response(text: str, status: str = "completed"):
    return NS(status=status, output_text=text)


def test_parser_accepts_only_items_grounded_in_allowed_message_ids():
    payload = {
        "items": [
            {
                "content": "사용자는 앞으로 답변을 짧게 받기를 원한다.",
                "kind": "preference",
                "disclosure": "global",
                "confidence": 0.98,
                "source_message_ids": ["101"],
            },
            {
                "content": "근거 없는 제3자 사실",
                "kind": "fact",
                "disclosure": "local",
                "confidence": 0.9,
                "source_message_ids": ["999"],
            },
            {
                "content": "잘못된 종류",
                "kind": "mood",
                "disclosure": "local",
                "confidence": 0.8,
                "source_message_ids": ["101"],
            },
        ]
    }

    parsed = parse_shadow_extraction(
        json.dumps(payload, ensure_ascii=False),
        allowed_source_ids={"101", "102"},
    )

    assert len(parsed.items) == 1
    assert parsed.rejected_items == 2
    item = parsed.items[0]
    assert item.kind == MemoryKind.PREFERENCE
    assert item.disclosure == MemoryDisclosure.GLOBAL
    assert item.source_message_ids == ("101",)


def test_parser_does_not_repair_malformed_output():
    parsed = parse_shadow_extraction(
        "사용자는 개발을 좋아함",
        allowed_source_ids={"101"},
    )

    assert parsed.items == ()
    assert parsed.rejected_items == 1


def test_build_shadow_turns_preserves_causal_context_and_provenance():
    pending = [{
        "message_id": "101",
        "created_at": "2026-09-17 10:00:00",
        "content": "응, 앞으로도 그렇게 해줘",
        "reply": "알겠어",
        "memory_context": json.dumps([{
            "kind": "replied_message",
            "role": "assistant",
            "ownership": "assistant",
            "content": "답변을 짧게 할까?",
        }], ensure_ascii=False),
    }]

    turns, source_ids, context_items = build_shadow_turns(pending, include_replies=True)

    assert source_ids == {"101"}
    assert context_items == 1
    assert turns[0]["message_id"] == "101"
    assert turns[0]["hina"] == "알겠어"
    assert turns[0]["context"][0]["ownership"] == "assistant"


def test_persist_shadow_items_suppresses_exact_retry_duplicates():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    item = ExtractedMemoryItem(
        content="사용자는 앞으로 답변을 짧게 받기를 원한다.",
        kind=MemoryKind.PREFERENCE,
        disclosure=MemoryDisclosure.GLOBAL,
        confidence=0.98,
        source_message_ids=("101",),
    )

    assert persist_shadow_items(store, scope, (item,)) == (1, 0)
    assert persist_shadow_items(store, scope, (item,)) == (0, 1)
    rows = store.memory_items(100)
    assert len(rows) == 1
    assert rows[0].source_message_ids == ("101",)
    store.close()


@pytest.mark.asyncio
async def test_summary_commits_legacy_memory_then_persists_shadow_items():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    store.add(scope, 101, "나는 커피보다 차를 좋아해", "그렇구나")
    store.add(scope, 102, "앞으로 음료 추천할 때도 기억해줘", "알겠어")
    extraction = json.dumps({
        "items": [{
            "content": "사용자는 커피보다 차를 선호하며 이후 음료 추천에도 반영되기를 원한다.",
            "kind": "preference",
            "disclosure": "global",
            "confidence": 0.97,
            "source_message_ids": ["101", "102"],
        }]
    }, ensure_ascii=False)
    harness = _harness(_response("사용자는 커피보다 차를 선호함"), _response(extraction))

    await harness.summarize(store, scope)

    assert store.summary(scope)[0] == "사용자는 커피보다 차를 선호함"
    rows = store.memory_items(100)
    assert len(rows) == 1
    assert rows[0].kind == MemoryKind.PREFERENCE
    assert rows[0].source_message_ids == ("101", "102")
    operations = [call.args[1] for call in harness.usage.request.await_args_list]
    assert operations == ["summarize", "extract_memory_items_shadow"]
    store.close()


@pytest.mark.asyncio
async def test_shadow_failure_never_rolls_back_or_fails_legacy_summary():
    store = Store(":memory:", history_turns=12)
    scope = Scope(20, 10, 100, True)
    store.add(scope, 201, "프로젝트 A를 진행 중이야", "응")
    store.add(scope, 202, "아직 끝나지 않았어", "확인했어")
    harness = _harness(
        _response("사용자는 프로젝트 A를 진행 중임"),
        RuntimeError("shadow extractor unavailable"),
    )

    await harness.summarize(store, scope)

    assert store.summary(scope)[0] == "사용자는 프로젝트 A를 진행 중임"
    assert store.memory_items(100) == []
    operations = [call.args[1] for call in harness.usage.request.await_args_list]
    assert operations == ["summarize", "extract_memory_items_shadow"]
    store.close()
