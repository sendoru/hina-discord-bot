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


def _settings(**overrides):
    values = {
        "summary_every": 8,
        "structured_memory_every": 4,
        "model": "fixed-model",
        "model_routing_mode": "fixed",
        "memory_routing_smart_threshold": 2.0,
        "fast_model": "fast-model",
        "smart_model": "smart-model",
        "memory_output_tokens": 4096,
        "gemini_thinking_level": "low",
        "gemini_fast_thinking_level": "minimal",
        "gemini_smart_thinking_level": "medium",
        "provider": "openai",
    }
    values.update(overrides)
    return NS(**values)


class Harness(MemorySummaryMixin):
    pass


def _harness(*responses, **settings):
    harness = Harness()
    harness.settings = _settings(**settings)
    harness.client = object()
    harness.usage = NS(
        request=AsyncMock(side_effect=responses),
        routing_event=lambda *args, **kwargs: None,
    )
    return harness


def _response(text: str, status: str = "completed"):
    return NS(status=status, output_text=text)


def _add_turns(store, scope, start: int, count: int):
    for message_id in range(start, start + count):
        store.add(scope, message_id, f"message-{message_id}", f"reply-{message_id}")


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

    assert parsed.valid
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

    assert not parsed.valid
    assert parsed.items == ()
    assert parsed.rejected_items == 1


def test_parser_accepts_explicit_empty_item_list():
    parsed = parse_shadow_extraction(
        '{"items":[]}',
        allowed_source_ids={"101"},
    )

    assert parsed.valid
    assert parsed.items == ()
    assert parsed.rejected_items == 0


def test_build_shadow_turns_preserves_causal_context_and_provenance():
    pending = [{
        "message_id": "101",
        "created_at": "2026-09-17 10:00:00",
        "content": "응, 앞으로도 그렇게 해줘",
        "reply": "알겠어",
        "exportable": 1,
        "memory_context": json.dumps([{
            "kind": "replied_message",
            "role": "assistant",
            "ownership": "assistant",
            "content": "답변을 짧게 할까?",
        }], ensure_ascii=False),
    }]

    turns, source_ids, context_items, source_public_at_capture = build_shadow_turns(
        pending,
        include_replies=True,
    )

    assert source_ids == {"101"}
    assert source_public_at_capture == {"101": True}
    assert context_items == 1
    assert turns[0]["message_id"] == "101"
    assert turns[0]["public_at_capture"] is True
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


def test_persist_shadow_items_never_upgrades_private_source_visibility():
    store = Store(":memory:")
    scope = Scope(20, 10, 100, True)
    item = ExtractedMemoryItem(
        content="사용자는 프로젝트 A를 진행 중이다.",
        kind=MemoryKind.TASK,
        disclosure=MemoryDisclosure.REFERENCE_GATED,
        confidence=0.95,
        source_message_ids=("201", "202"),
    )

    stored, duplicates = persist_shadow_items(
        store,
        scope,
        (item,),
        source_public_at_capture={"201": True, "202": False},
    )

    assert (stored, duplicates) == (1, 0)
    rows = store.memory_items(100)
    assert len(rows) == 1
    assert rows[0].origin_public_at_capture is False
    store.close()


def test_new_cursor_baselines_from_existing_legacy_summary():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _add_turns(store, scope, 101, 6)
    history = store.history(scope)
    store.save_summary(scope, "기존 요약", history[3]["id"])

    baseline = store.memory_extraction_cursor(scope)
    assert baseline == history[3]["id"]
    pending = store.pending_memory_extraction(scope)
    assert [row["message_id"] for row in pending] == ["105", "106"]

    store.save_summary(scope, "더 최신 요약", history[-1]["id"])
    assert store.memory_extraction_cursor(scope) == baseline
    store.close()


@pytest.mark.asyncio
async def test_structured_extraction_runs_at_four_turns_without_legacy_summary():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _add_turns(store, scope, 101, 4)
    extraction = json.dumps({
        "items": [{
            "content": "사용자는 테스트용 사실을 말했다.",
            "kind": "fact",
            "disclosure": "local",
            "confidence": 0.9,
            "source_message_ids": ["101"],
        }]
    }, ensure_ascii=False)
    harness = _harness(_response(extraction))

    await harness.extract_structured_memory(store, scope)

    assert store.summary(scope) == ("", 0)
    assert store.memory_extraction_cursor(scope) == store.history(scope)[-1]["id"]
    rows = store.memory_items(100)
    assert len(rows) == 1
    assert rows[0].source_message_ids == ("101",)
    operations = [call.args[1] for call in harness.usage.request.await_args_list]
    assert operations == ["extract_memory_items_shadow"]
    store.close()


@pytest.mark.asyncio
async def test_structured_extraction_processes_fixed_size_batches():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _add_turns(store, scope, 101, 8)
    harness = _harness(
        _response('{"items":[]}'),
        _response('{"items":[]}'),
    )

    await harness.extract_structured_memory(store, scope)
    first_cursor = store.memory_extraction_cursor(scope)
    assert [row["message_id"] for row in store.pending_memory_extraction(scope)] == [
        "105", "106", "107", "108"
    ]

    await harness.extract_structured_memory(store, scope)
    assert store.memory_extraction_cursor(scope) > first_cursor
    assert store.pending_memory_extraction(scope) == []
    store.close()


@pytest.mark.asyncio
async def test_valid_empty_extraction_advances_cursor():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _add_turns(store, scope, 101, 4)
    harness = _harness(_response('{"items":[]}'))

    await harness.extract_structured_memory(store, scope)

    assert store.memory_extraction_cursor(scope) == store.history(scope)[-1]["id"]
    assert store.memory_items(100) == []
    store.close()


@pytest.mark.asyncio
async def test_invalid_extraction_keeps_cursor_for_retry():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _add_turns(store, scope, 101, 4)
    harness = _harness(
        _response("not-json"),
        _response('{"items":[]}'),
    )

    await harness.extract_structured_memory(store, scope)
    assert store.memory_extraction_cursor(scope) == 0
    assert len(store.pending_memory_extraction(scope)) == 4

    await harness.extract_structured_memory(store, scope)
    assert store.memory_extraction_cursor(scope) == store.history(scope)[-1]["id"]
    store.close()


@pytest.mark.asyncio
async def test_extractor_failure_does_not_advance_cursor():
    store = Store(":memory:", history_turns=12)
    scope = Scope(20, 10, 100, True)
    _add_turns(store, scope, 201, 4)
    harness = _harness(RuntimeError("shadow extractor unavailable"))

    await harness.extract_structured_memory(store, scope)

    assert store.memory_extraction_cursor(scope) == 0
    assert len(store.pending_memory_extraction(scope)) == 4
    assert store.memory_items(100) == []
    store.close()


@pytest.mark.asyncio
async def test_legacy_summary_and_structured_cursor_advance_independently():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _add_turns(store, scope, 101, 4)
    harness = _harness(
        _response('{"items":[]}'),
        _response("사용자의 기존 장기 기억"),
    )

    await harness.extract_structured_memory(store, scope)
    structured_through = store.memory_extraction_cursor(scope)
    assert store.summary(scope) == ("", 0)

    _add_turns(store, scope, 105, 4)
    await harness.summarize(store, scope)

    assert store.memory_extraction_cursor(scope) == structured_through
    assert store.summary(scope)[0] == "사용자의 기존 장기 기억"
    operations = [call.args[1] for call in harness.usage.request.await_args_list]
    assert operations == ["extract_memory_items_shadow", "summarize"]
    store.close()


def test_reconciliation_candidates_are_same_user_same_realm_same_channel_only():
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    sibling = Scope(1, 20, 100, True)
    other_user = Scope(1, 10, 200, True)
    keep_id = store.add_memory_item(
        scope,
        "same space",
        kind=MemoryKind.FACT,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("1",),
    )
    store.add_memory_item(
        sibling,
        "sibling channel",
        kind=MemoryKind.FACT,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("2",),
    )
    store.add_memory_item(
        other_user,
        "other user",
        kind=MemoryKind.FACT,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("3",),
    )

    candidates = store.memory_reconciliation_candidates(scope)

    assert [item.id for item in candidates] == [keep_id]
    assert [item.content for item in candidates] == ["same space"]
    store.close()


def test_parser_keeps_item_but_drops_relation_to_unknown_target():
    parsed = parse_shadow_extraction(
        json.dumps({
            "items": [{
                "content": "사용자는 새로운 사실을 말했다.",
                "kind": "fact",
                "disclosure": "local",
                "confidence": 0.9,
                "source_message_ids": ["101"],
                "relation": {
                    "type": "corrects",
                    "target_item_id": 999,
                    "confidence": 0.95,
                },
            }]
        }, ensure_ascii=False),
        allowed_source_ids={"101"},
        allowed_target_item_ids={12},
    )

    assert parsed.valid
    assert len(parsed.items) == 1
    assert parsed.items[0].relation is None
    assert parsed.rejected_relations == 1


@pytest.mark.asyncio
async def test_shadow_reconciliation_records_correction_without_mutating_old_item():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    old_id = store.add_memory_item(
        scope,
        "사용자는 '키위'라는 고양이를 키운다.",
        kind=MemoryKind.FACT,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("90",),
        confidence=0.9,
    )
    _add_turns(store, scope, 101, 4)
    extraction = json.dumps({
        "items": [{
            "content": "사용자는 '웅'이라는 고양이를 키운다.",
            "kind": "fact",
            "disclosure": "local",
            "confidence": 0.99,
            "source_message_ids": ["101", "102"],
            "relation": {
                "type": "corrects",
                "target_item_id": old_id,
                "confidence": 0.99,
            },
        }]
    }, ensure_ascii=False)
    harness = _harness(_response(extraction))

    await harness.extract_structured_memory(store, scope)

    items = store.memory_items(100)
    assert [item.content for item in items] == [
        "사용자는 '키위'라는 고양이를 키운다.",
        "사용자는 '웅'이라는 고양이를 키운다.",
    ]
    assert items[0].id == old_id
    request = harness.usage.request.await_args.kwargs
    payload = json.loads(request["input"])
    assert payload["existing_memory_candidates"] == [{
        "id": old_id,
        "content": "사용자는 '키위'라는 고양이를 키운다.",
        "kind": "fact",
        "disclosure": "local",
        "confidence": 0.9,
    }]

    proposals = store.memory_reconciliation_proposals(100)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["new_memory_item_id"] == items[1].id
    assert proposal["target_memory_item_id"] == old_id
    assert proposal["relation"] == "corrects"
    assert proposal["confidence"] == pytest.approx(0.99)
    assert json.loads(proposal["source_message_ids"]) == ["101", "102"]
    store.close()


@pytest.mark.asyncio
async def test_partial_retry_does_not_offer_same_batch_item_as_reconciliation_candidate():
    store = Store(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    old_id = store.add_memory_item(
        scope,
        "사용자는 차를 좋아한다.",
        kind=MemoryKind.PREFERENCE,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("90",),
    )
    _add_turns(store, scope, 101, 4)
    first = json.dumps({
        "items": [{
            "content": "사용자는 녹차를 좋아한다.",
            "kind": "preference",
            "disclosure": "local",
            "confidence": 0.9,
            "source_message_ids": ["101"],
            "relation": {
                "type": "corrects",
                "target_item_id": old_id,
                "confidence": 0.8,
            },
        }]
    }, ensure_ascii=False)
    harness = _harness(_response(first), _response(first))

    original_writer = store.add_memory_reconciliation_proposal
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("proposal write failed")
        return original_writer(*args, **kwargs)

    store.add_memory_reconciliation_proposal = fail_once
    await harness.extract_structured_memory(store, scope)
    assert store.memory_extraction_cursor(scope) == 0

    await harness.extract_structured_memory(store, scope)

    second_payload = json.loads(harness.usage.request.await_args_list[-1].kwargs["input"])
    candidate_ids = {row["id"] for row in second_payload["existing_memory_candidates"]}
    assert old_id in candidate_ids
    new_item = next(item for item in store.memory_items(100) if item.id != old_id)
    assert new_item.id not in candidate_ids
    assert len(store.memory_reconciliation_proposals(100)) == 1
    store.close()


def test_forget_removes_reconciliation_proposals_with_memory():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    old_id = store.add_memory_item(
        scope,
        "old",
        kind=MemoryKind.FACT,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("1",),
    )
    new_id = store.add_memory_item(
        scope,
        "new",
        kind=MemoryKind.FACT,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("2",),
    )
    store.add_memory_reconciliation_proposal(
        scope,
        new_memory_item_id=new_id,
        target_memory_item_id=old_id,
        relation="corrects",
        confidence=0.9,
        source_message_ids=("2",),
    )

    store.forget(scope)

    assert store.memory_items(100) == []
    assert store.memory_reconciliation_proposals(100) == []
    store.close()
