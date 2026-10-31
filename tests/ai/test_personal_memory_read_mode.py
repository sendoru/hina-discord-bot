"""PERSONAL_MEMORY_READ_MODE wiring through the real request assembly (#457)."""

import json
from dataclasses import replace

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.ai.reference_context_blocks import read_reference_context
from hina_bot.core.config import Settings
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE, CURRENT_MEMORY_SELECTION
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

DM = Scope(None, 10, 100)
SERVER = Scope(1, 20, 100, True)


def _client(calls):
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp", "object": "response", "created_at": 0, "status": "completed",
            "model": "gpt-4.1-mini",
            "output": [{"type": "message", "id": "m", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "응", "annotations": []}]}],
        })

    return AsyncOpenAI(api_key="test-not-a-real-key",
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _annotate(store, item_id, **fields):
    cited = [{"user": fields.pop("quote", ""), "at": "2026-10-20 03:00:00"}]
    annotation = parse_annotation(fields, MemoryKind.FACT, cited)
    store.upsert_memory_annotation(replace(annotation, memory_item_id=item_id))


def _seed(store):
    food = store.add_memory_item(DM, "매운 음식을 좋아함", kind=MemoryKind.PREFERENCE,
                                 disclosure=MemoryDisclosure.LOCAL)
    _annotate(store, food, quote="매운 음식 좋아", evidence="매운 음식 좋아", time_scope="ongoing",
              stability="long", usage_mode="ambient", keywords=["음식", "매운", "배고픔"])
    work = store.add_memory_item(DM, "사내 결제 시스템 마이그레이션 담당", kind=MemoryKind.FACT,
                                 disclosure=MemoryDisclosure.LOCAL)
    _annotate(store, work, time_scope="ongoing", stability="long", usage_mode="contextual",
              keywords=["회사", "결제", "마이그레이션"])
    store.add_memory_item(DM, "가족 얘기는 먼저 꺼내지 말아 달라고 함", kind=MemoryKind.BOUNDARY,
                          disclosure=MemoryDisclosure.LOCAL)
    store.add_memory_item(DM, "가벼운 장난을 주고받음", kind=MemoryKind.RELATIONSHIP,
                          disclosure=MemoryDisclosure.IMPLICIT,
                          relationship_evidence={"comfort": 2})


async def _answer(mode, scope, message):
    calls = []
    llm = LLM(Settings(discord_token="test", openai_api_key="test",
                       external_context_policy="full", personal_memory_read_mode=mode),
              client=_client(calls))
    store = Store.create(":memory:")
    try:
        _seed(store)
        await llm.answer(store, scope, "사용자", message, channel_context=[])
        return calls[-1], CURRENT_CONTEXT_PROVENANCE.get(), CURRENT_MEMORY_SELECTION.get()
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_tiered_dm_sends_only_selected_memory_with_its_policy():
    payload, provenance, selection = await _answer("tiered", DM, "아 배고프다")

    reference = read_reference_context(payload["input"][0]["content"])
    memory = reference["user_memory"]
    assert memory["directives"] == ["가족 얘기는 먼저 꺼내지 말아 달라고 함"]
    assert [note["text"] for note in memory["notes"]] == ["매운 음식을 좋아함"]
    assert reference["relationship_profile"] == {"comfort": 2}
    raw = json.dumps(reference, ensure_ascii=False)
    assert "마이그레이션" not in raw and "가벼운 장난" not in raw
    assert "structured_owner_memory" not in reference
    assert "[사용자 기억]" in payload["instructions"]
    assert "[구조화 사용자 기억]" not in payload["instructions"]
    assert provenance["memory_selection"]["tiers"] == {"T0": 1, "T2": 1}
    assert {row["store"] for row in provenance["structured_memory"]} == {"personal"}
    assert selection is not None


@pytest.mark.asyncio
async def test_tiered_policy_stays_when_nothing_is_selected():
    payload, provenance, _ = await _answer("tiered", SERVER, "오늘 날씨 좋다")

    reference = read_reference_context(payload["input"][0]["content"])
    # DM-local items stay hidden in the server; the DM boundary applies quietly.
    assert reference["user_memory"]["notes"] == []
    assert reference["user_memory"]["quiet_boundaries"] == ["가족 얘기는 먼저 꺼내지 말아 달라고 함"]
    assert "[사용자 기억]" in payload["instructions"]
    assert provenance["memory_selection"]["excluded"]["permission"] == 2


@pytest.mark.asyncio
async def test_shadow_answers_with_legacy_memory_and_records_the_selection():
    payload, provenance, selection = await _answer("shadow", DM, "아 배고프다")

    reference = read_reference_context(payload["input"][0]["content"])
    assert "user_memory" not in reference
    assert {row["content"] for row in reference["structured_owner_memory"]} >= {
        "사내 결제 시스템 마이그레이션 담당", "매운 음식을 좋아함",
    }
    assert "[구조화 사용자 기억]" in payload["instructions"]
    assert provenance["memory_selection"]["mode"] == "shadow"
    assert provenance["memory_selection"]["tiers"] == {"T0": 1, "T2": 1}
    # Nothing was injected from the selection, so no usage may be recorded for it.
    assert selection is None


@pytest.mark.asyncio
async def test_legacy_mode_is_unchanged_and_records_no_selection():
    payload, provenance, selection = await _answer("legacy", DM, "아 배고프다")

    reference = read_reference_context(payload["input"][0]["content"])
    assert "structured_owner_memory" in reference and "user_memory" not in reference
    assert "memory_selection" not in provenance
    assert selection is None
