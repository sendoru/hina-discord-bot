"""PERSONAL_MEMORY_READ_MODE wiring through the real request assembly (#457)."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai import request_assembly
from hina_bot.ai.decisions import DecisionsClient
from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.ai.reference_context_blocks import read_reference_context
from hina_bot.ai.request_assembly import (
    STRUCTURED_MEMORY_POLICY,
    TIERED_MEMORY_POLICY,
    WORLD_CORE_POLICY,
)
from hina_bot.ai.runtime_context import build_runtime_context
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
    assert TIERED_MEMORY_POLICY.strip() in payload["instructions"]
    assert STRUCTURED_MEMORY_POLICY.strip() not in payload["instructions"]
    assert provenance["memory_selection"]["tiers"] == {"T0": 1, "T2": 1}
    assert {row["store"] for row in provenance["structured_memory"]} == {"personal"}
    assert selection.mode == "tiered" and selection.legacy_notes == ()


@pytest.mark.asyncio
async def test_tiered_policy_stays_when_nothing_is_selected():
    payload, provenance, _ = await _answer("tiered", SERVER, "오늘 날씨 좋다")

    reference = read_reference_context(payload["input"][0]["content"])
    # DM-local items stay hidden in the server; the DM boundary applies quietly.
    assert reference["user_memory"]["notes"] == []
    assert reference["user_memory"]["quiet_boundaries"] == ["가족 얘기는 먼저 꺼내지 말아 달라고 함"]
    assert TIERED_MEMORY_POLICY.strip() in payload["instructions"]
    assert provenance["memory_selection"]["excluded"]["permission"] == 2


@pytest.mark.asyncio
async def test_shadow_answers_with_legacy_memory_and_records_the_selection():
    payload, provenance, selection = await _answer("tiered_shadow", DM, "아 배고프다")

    reference = read_reference_context(payload["input"][0]["content"])
    assert "user_memory" not in reference
    assert {row["content"] for row in reference["structured_owner_memory"]} >= {
        "사내 결제 시스템 마이그레이션 담당", "매운 음식을 좋아함",
    }
    assert STRUCTURED_MEMORY_POLICY.strip() in payload["instructions"]
    assert provenance["memory_selection"]["mode"] == "tiered_shadow"
    assert provenance["memory_selection"]["tiers"] == {"T0": 1, "T2": 1}
    # Nothing was injected from the selection: the run only carries the coverage reference
    # (every FULL non-directive note legacy sent), never usage to record.
    assert selection.mode == "tiered_shadow"
    assert {item.content for item, _annotation in selection.legacy_notes} == {
        "사내 결제 시스템 마이그레이션 담당", "매운 음식을 좋아함",
    }


@pytest.mark.asyncio
async def test_legacy_mode_is_unchanged_and_records_no_selection():
    payload, provenance, selection = await _answer("legacy", DM, "아 배고프다")

    reference = read_reference_context(payload["input"][0]["content"])
    assert "structured_owner_memory" in reference and "user_memory" not in reference
    assert "memory_selection" not in provenance
    assert selection is None


@pytest.mark.asyncio
async def test_tiered_profile_and_carried_topic_reach_the_request():
    calls = []
    llm = LLM(Settings(discord_token="test", openai_api_key="test",
                       external_context_policy="full", personal_memory_read_mode="tiered"),
              client=_client(calls))
    store = Store.create(":memory:")
    try:
        _seed(store)
        cat = store.add_memory_item(DM, "고양이 보리를 키움", kind=MemoryKind.FACT,
                                    disclosure=MemoryDisclosure.LOCAL)
        _annotate(store, cat, quote="고양이 보리 키워", evidence="고양이 보리 키워",
                  time_scope="ongoing", stability="long", usage_mode="ambient", importance=3,
                  keywords=["고양이", "보리"])
        # The user's previous message here carries the work topic into "이거 언제 끝나냐".
        store.record_completed_turn(DM, 1, "회사 결제 건 때문에 야근 중", "고생 많네", name="사용자")
        await llm.answer(store, DM, "사용자", "이거 언제 끝나냐", channel_context=[])
        payload, provenance = calls[-1], CURRENT_CONTEXT_PROVENANCE.get()
    finally:
        await llm.close()
        store.close()

    memory = read_reference_context(payload["input"][0]["content"])["user_memory"]
    assert memory["profile"] == ["고양이 보리를 키움"]
    assert [note["text"] for note in memory["notes"]] == ["사내 결제 시스템 마이그레이션 담당"]
    items = provenance["memory_selection"]["items"]
    assert {(row["tier"], row.get("via")) for row in items} >= {("Tp", None), ("T2", "carry")}


async def _decision_answer(
    store, scope, message, *, lore="decision", memory="decision", handler, **settings,
):
    calls = []
    llm = LLM(Settings(
        discord_token="test", openai_api_key="test", gemini_api_key="decision-test",
        rag_mode=lore, personal_memory_read_mode=memory, **settings,
    ), client=_client(calls))
    await llm.decisions_client.close()
    llm.decisions_client = DecisionsClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        await llm.answer(store, scope, "사용자", message)
        payload = calls[-1]
        return (
            payload,
            read_reference_context(payload["input"][0]["content"]),
            CURRENT_CONTEXT_PROVENANCE.get(),
            CURRENT_MEMORY_SELECTION.get(),
        )
    finally:
        await llm.close()


def _decisions_reply(body, choose):
    candidates = json.loads(body["input"])["candidates"]
    assert set(body["questions"]) == candidates.keys()
    return httpx.Response(200, json={
        "answers": {
            key: {"type": "noul", "noul": 0.99 if choose(row) else 0.01, "confidence": 0.99}
            for key, row in candidates.items()
        },
        "usage": {"total_input_tokens": 8000},
    })


async def test_decisions_evaluate_both_full_pools_concurrently_and_keep_core():
    store = Store.create(":memory:")
    entered = asyncio.Event()
    bodies = {}
    try:
        _seed(store)

        async def handler(request):
            body = json.loads(request.content)
            candidates = json.loads(body["input"])["candidates"]
            domain = "memory" if next(iter(candidates.values())).get("state") else "lore"
            bodies[domain] = candidates
            if len(bodies) == 2:
                entered.set()
            await asyncio.wait_for(entered.wait(), 0.5)
            return _decisions_reply(
                body, lambda row: "마이그레이션" in row["content"] if domain == "memory"
                else "생일" in row["content"] and "히나" in row["subjects"],
            )

        payload, reference, trace, run = await _decision_answer(
            store, DM, "그 프로젝트 어떻게 하지?", handler=handler, community_lore=False,
        )
    finally:
        store.close()
    assert len(bodies["lore"]) > 100
    assert all(row["awareness"] != "audience_only" for row in bodies["lore"].values())
    assert all(row["kind"] != "fandom" for row in bodies["lore"].values())
    assert {row["content"] for row in bodies["memory"].values()} == {
        "사내 결제 시스템 마이그레이션 담당", "매운 음식을 좋아함",
    }
    assert reference["user_memory"]["notes"][0]["text"] == "사내 결제 시스템 마이그레이션 담당"
    assert reference["user_memory"]["notes"][0]["use"] == "if_relevant"
    assert reference["user_memory"]["directives"] == ["가족 얘기는 먼저 꺼내지 말아 달라고 함"]
    assert reference["relationship_profile"] == {"comfort": 2}
    assert WORLD_CORE_POLICY.strip() in payload["instructions"]
    assert TIERED_MEMORY_POLICY.strip() in payload["instructions"]
    lore = trace["retrieval_decisions"]["lore"]
    assert lore["decision_candidates"] == len(bodies["lore"])
    assert lore["decision_selected"] < len(bodies["lore"])
    assert lore["decision_applied"] and lore["decision_input_tokens"] == 8000
    assert trace["retrieval_decisions"]["memory"]["decision_selected"] == 1
    assert run.mode == "decision"


async def test_decision_shadow_matches_legacy_answer_and_keeps_metrics_private(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        request_assembly, "build_runtime_context",
        lambda settings: build_runtime_context(settings, now=datetime(2026, 11, 13, tzinfo=UTC)),
    )
    store = Store.create(":memory:")
    log = tmp_path / "usage.jsonl"
    try:
        _seed(store)

        def handler(request):
            return _decisions_reply(json.loads(request.content), lambda row: True)

        def unused(_request):
            pytest.fail("legacy must not contact Decisions")

        baseline, _, _, _ = await _decision_answer(
            store, DM, "current private utterance", lore="full", memory="legacy", handler=unused,
        )
        shadow, _, trace, run = await _decision_answer(
            store, DM, "current private utterance", lore="decision_shadow",
            memory="decision_shadow", handler=handler, usage_log_path=str(log),
        )
    finally:
        store.close()

    assert shadow["instructions"] == baseline["instructions"]
    assert shadow["input"] == baseline["input"]
    assert run.mode == "decision_shadow" and run.legacy_notes
    assert all(not row["decision_applied"] for row in trace["retrieval_decisions"].values())
    raw = log.read_text()
    assert "current private utterance" not in raw and "마이그레이션" not in raw
    metrics = [row for line in raw.splitlines() if (row := json.loads(line))["operation"].startswith("decision.")]
    assert {row["operation"] for row in metrics} == {"decision.lore", "decision.memory"}
    assert all(row["input_tokens"] == 8000 and "selected_ids" not in row for row in metrics)


async def test_decision_error_fallback_and_successful_empty_are_distinct():
    store = Store.create(":memory:")
    try:
        _seed(store)

        def unavailable(_request):
            return httpx.Response(503, text="provider request echo")

        def all_no(request):
            return _decisions_reply(json.loads(request.content), lambda row: False)

        failure, ref_failed, trace_failed, _ = await _decision_answer(
            store, DM, "어떻게 생각해?", handler=unavailable,
        )
        empty, ref_empty, trace_empty, _ = await _decision_answer(
            store, DM, "어떻게 생각해?", handler=all_no,
        )
    finally:
        store.close()

    assert "[검수된 정적 설정 자료" in failure["instructions"]
    assert "[검수된 정적 설정 자료" not in empty["instructions"]
    assert ref_empty["user_memory"]["notes"] == []
    assert len(ref_failed["user_memory"]["notes"]) == 2
    for reference in (ref_failed, ref_empty):
        assert reference["user_memory"]["directives"]
    assert trace_failed["retrieval_decisions"]["lore"]["decision_fallback"] == "full"
    assert trace_failed["retrieval_decisions"]["memory"]["decision_fallback"] == "scope_safe_legacy"
    assert trace_empty["retrieval_decisions"]["lore"]["decision_applied"]
    assert trace_empty["retrieval_decisions"]["memory"]["decision_applied"]


async def test_decision_memory_scope_sensitive_reference_and_opt_out():
    store = Store.create(":memory:")
    bodies = []
    try:
        _seed(store)
        store.add_memory_item(
            SERVER, "이 서버 공개 사실", kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        sensitive = store.add_memory_item(
            SERVER, "민감한 진료 일정", kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        _annotate(store, sensitive, usage_mode="sensitive", keywords=["진료"])
        store.add_memory_item(
            Scope(2, 22, SERVER.user_id, True), "다른 서버 비공개 사실", kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        store.add_memory_item(
            Scope(1, 20, 900, True), "다른 사용자 사실", kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        gated = store.add_memory_item(
            DM, "오로라 엔진 프로젝트를 진행 중", kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        deleted = store.add_memory_item(
            SERVER, "철회된 내용", kind=MemoryKind.FACT, disclosure=MemoryDisclosure.LOCAL,
        )
        store.retract_memory_item(deleted, expected_revision=0)

        def handler(request):
            body = json.loads(request.content)
            bodies.append(json.loads(body["input"])["candidates"])
            return _decisions_reply(body, lambda row: True)

        _, reference, trace, _ = await _decision_answer(
            store, SERVER, "내가 DM에서 말한 오로라 엔진 기억나?", lore="full", handler=handler,
        )
        assert {row["content"] for row in bodies[-1].values()} == {"이 서버 공개 사실"}
        assert reference["user_memory"]["quiet_boundaries"] == [
            "가족 얘기는 먼저 꺼내지 말아 달라고 함",
        ]
        assert any(
            row["item_id"] == gated and row["tier"] == "T4"
            for row in trace["memory_selection"]["items"]
        )

        _, reference, _, _ = await _decision_answer(
            store, SERVER, "진료 일정이 어땠지?", lore="full", handler=handler,
        )
        assert any(note["use"] == "only_if_user_raises" for note in reference["user_memory"]["notes"])

        store.set_user_memory_mode(SERVER.user_id, "off")
        _, reference, trace, run = await _decision_answer(
            store, SERVER, "진료 일정이 어땠지?", lore="full",
            handler=lambda _: pytest.fail("owner opted out"),
        )
        assert "user_memory" not in reference and run is None
        assert trace["retrieval_decisions"]["memory"]["decision_reason"] == "memory_disabled"
    finally:
        store.close()


async def test_retracted_candidate_during_http_request_cannot_reach_answer():
    store = Store.create(":memory:")
    try:
        item = store.add_memory_item(
            DM, "삭제된 예약", kind=MemoryKind.FACT, disclosure=MemoryDisclosure.LOCAL,
        )

        def handler(request):
            store.retract_memory_item(item, expected_revision=0)
            return _decisions_reply(json.loads(request.content), lambda row: True)

        _, reference, trace, _ = await _decision_answer(
            store, DM, "내 예약 언제야?", lore="full", handler=handler,
        )
        assert "삭제된 예약" not in json.dumps(reference, ensure_ascii=False)
        assert trace["retrieval_decisions"]["memory"]["decision_fallback"] == "memory_context_changed"
        assert trace["retrieval_decisions"]["memory"]["selected_ids"] == []
    finally:
        store.close()
