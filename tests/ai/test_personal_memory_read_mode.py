"""PERSONAL_MEMORY_READ_MODE wiring through the real request assembly (#457)."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.decisions import DecisionsClient
from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.ai.reference_context_blocks import read_reference_context
from hina_bot.ai.request_assembly import (
    WORLD_CORE_POLICY,
)
from hina_bot.core.config import Settings
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE, CURRENT_MEMORY_SELECTION
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.runtime_config import RuntimeSettings
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


async def test_full_mode_records_no_selection():
    payload, provenance, selection = await _answer("full", DM, "아 배고프다")

    reference = read_reference_context(payload["input"][0]["content"])
    assert "structured_owner_memory" in reference and "user_memory" not in reference
    assert "memory_selection" not in provenance
    assert selection is None


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
        "가족 얘기는 먼저 꺼내지 말아 달라고 함", "가벼운 장난을 주고받음",
    }
    assert [row["content"] for row in reference["structured_owner_memory"]] == [
        "사내 결제 시스템 마이그레이션 담당",
    ]
    assert WORLD_CORE_POLICY.strip() in payload["instructions"]
    lore = trace["retrieval_decisions"]["lore"]
    assert lore["decision_candidates"] == len(bodies["lore"])
    assert lore["decision_selected"] < len(bodies["lore"])
    assert lore["decision_applied"] and lore["decision_input_tokens"] == 8000
    assert trace["retrieval_decisions"]["memory"]["decision_selected"] == 1
    assert run.mode == "decision"


async def test_decisions_runtime_timeout_change_affects_both_requests_without_restart():
    store = Store.create(":memory:")
    _seed(store)
    settings = RuntimeSettings(Settings(
        discord_token="test", openai_api_key="test", gemini_api_key="decision-test",
        rag_mode="decision", personal_memory_read_mode="decision", community_lore=False,
    ), store)
    calls = []
    llm = LLM(settings, client=_client(calls))
    await llm.decisions_client.close()

    async def delayed(request):
        await asyncio.sleep(0.35)
        return _decisions_reply(json.loads(request.content), lambda row: True)

    llm.decisions_client = DecisionsClient(
        httpx.AsyncClient(transport=httpx.MockTransport(delayed)),
    )
    try:
        settings.set_text("DECISION_TIMEOUT_SECONDS", "0.25")
        await llm.answer(store, DM, "사용자", "마이그레이션 계획 알려줘")
        trace = CURRENT_CONTEXT_PROVENANCE.get()["retrieval_decisions"]
        assert {trace[domain]["decision_reason"] for domain in ("lore", "memory")} == {"timeout"}
        assert {trace[domain]["decision_fallback"] for domain in ("lore", "memory")} == {"full"}

        settings.set_text("DECISION_TIMEOUT_SECONDS", "1.0")
        await llm.answer(store, DM, "사용자", "마이그레이션 계획 알려줘")
        trace = CURRENT_CONTEXT_PROVENANCE.get()["retrieval_decisions"]
        assert all(trace[domain]["decision_applied"] for domain in ("lore", "memory"))
        assert all(trace[domain]["decision_reason"] == "" for domain in ("lore", "memory"))
    finally:
        await llm.close()
        store.close()


async def test_decision_error_fallback_and_successful_empty_are_distinct():
    store = Store.create(":memory:")
    try:
        _seed(store)

        def unavailable(_request):
            return httpx.Response(503, text="provider request echo")

        def all_no(request):
            return _decisions_reply(json.loads(request.content), lambda row: False)

        _, manual_full, _, _ = await _decision_answer(
            store, DM, "어떻게 생각해?", memory="full", lore="full",
            handler=lambda _: pytest.fail("manual full called Decisions"),
        )
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
    assert not ref_empty.get("structured_owner_memory")
    assert ref_failed["structured_owner_memory"] == manual_full["structured_owner_memory"]
    assert len(ref_failed["structured_owner_memory"]) == 4
    assert trace_failed["retrieval_decisions"]["lore"]["decision_fallback"] == "full"
    assert trace_failed["retrieval_decisions"]["memory"]["decision_fallback"] == "full"
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
            disclosure=MemoryDisclosure.LOCAL,
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
        assert all(row["item_id"] != gated for row in trace["memory_selection"]["items"])
        assert "오로라 엔진" not in json.dumps(reference, ensure_ascii=False)

        _, reference, _, _ = await _decision_answer(
            store, SERVER, "진료 일정이 어땠지?", lore="full", handler=handler,
        )
        assert "민감한 진료 일정" not in json.dumps(reference, ensure_ascii=False)
        _, reference, _, _ = await _decision_answer(
            store, DM, "안녕", lore="full", handler=handler,
        )
        assert "민감한 진료 일정" in {row["content"] for row in reference["structured_owner_memory"]}

        def unavailable(_request):
            return httpx.Response(503)

        for mode in ("full", "decision"):
            _, reference, _, _ = await _decision_answer(
                store, SERVER, "내 진료 일정 기억나?", memory=mode, lore="full", handler=unavailable,
            )
            assert {r["content"] for r in reference["structured_full_memory"]} == {"이 서버 공개 사실"}

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


@pytest.mark.parametrize(("default", "opted_in"), [
    ("on", False), ("on", True), ("off", False), ("off", True),
])
async def test_cross_scope_policy_precedes_full_decision_and_fallback(default, opted_in):
    store = Store.create(":memory:")
    store.set_cross_scope_opt_in(DM.user_id, opted_in)
    try:
        for origin, content, disclosure in [
            (SERVER, "현재 공간", MemoryDisclosure.LOCAL),
            (DM, "공간 간 취향", MemoryDisclosure.GLOBAL),
            (Scope(2, 22, DM.user_id, True), "다른 서버에서 시작한 취미 프로젝트", MemoryDisclosure.GLOBAL),
            (DM, "비공개 사실", MemoryDisclosure.LOCAL),
            (DM, "공간 밖 일반 implicit 사실", MemoryDisclosure.IMPLICIT),
        ]:
            store.add_memory_item(origin, content, kind=MemoryKind.FACT, disclosure=disclosure)
        sensitive = store.add_memory_item(
            DM, "민감한 개인 정보", kind=MemoryKind.FACT, disclosure=MemoryDisclosure.GLOBAL,
        )
        _annotate(store, sensitive, usage_mode="sensitive")
        store.add_memory_item(
            Scope(None, 99, 900), "다른 사용자 취향", kind=MemoryKind.PREFERENCE,
            disclosure=MemoryDisclosure.GLOBAL,
        )
        expected = {"현재 공간"}
        if default == "on" or opted_in:
            expected.update({"공간 간 취향", "다른 서버에서 시작한 취미 프로젝트"})

        for mode, fail in [("full", False), ("decision", False), ("decision", True)]:
            def handler(request, mode=mode, fail=fail):
                assert mode == "decision"
                body = json.loads(request.content)
                rows = json.loads(body["input"])["candidates"]
                assert {row["content"] for row in rows.values()} == expected
                return httpx.Response(503) if fail else _decisions_reply(body, lambda row: True)

            _, reference, _, _ = await _decision_answer(
                store, SERVER, "뭐 할까?", lore="full", memory=mode, handler=handler,
                personal_memory_cross_scope=default,
            )
            assert {row["content"] for row in reference["structured_full_memory"]} == expected
        _, reference, _, _ = await _decision_answer(
            store, DM, "뭐 할까?", lore="full", memory="full",
            handler=lambda _: pytest.fail("manual full called Decisions"), personal_memory_cross_scope=default,
        )
        assert {row["content"] for row in reference["structured_owner_memory"]} == {
            "현재 공간", "공간 간 취향", "다른 서버에서 시작한 취미 프로젝트",
            "비공개 사실", "공간 밖 일반 implicit 사실", "민감한 개인 정보",
        }
        assert store.cross_scope_opt_in(DM.user_id) is opted_in
    finally:
        store.close()


async def test_opt_in_revoked_while_decision_pending_removes_cross_scope_memory():
    store = Store.create(":memory:")
    try:
        store.add_memory_item(DM, "다른 공간의 취향", kind=MemoryKind.FACT,
                              disclosure=MemoryDisclosure.GLOBAL)
        store.set_cross_scope_opt_in(DM.user_id, True)

        def revoke(request):
            store.set_cross_scope_opt_in(DM.user_id, False)
            return _decisions_reply(json.loads(request.content), lambda row: True)

        _, reference, trace, _ = await _decision_answer(
            store, SERVER, "내 취향", lore="full", handler=revoke,
            personal_memory_cross_scope="off",
        )
        assert not reference.get("structured_full_memory")
        assert trace["retrieval_decisions"]["memory"]["decision_fallback"] == "memory_context_changed"
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["full", "decision"])
def test_cross_space_behavior_never_transports_preference_text(mode):
    from hina_bot.ai.structured_memory_context import (
        personal_memory_fields,
        personal_memory_selection,
    )

    store = Store.create(":memory:")
    try:
        item_id = store.add_memory_item(DM, "private source marker", kind=MemoryKind.PREFERENCE,
                                        disclosure=MemoryDisclosure.IMPLICIT)
        _annotate(store, item_id, slot="praise_light", usage_mode="contextual")
        settings = Settings(discord_token="test", openai_api_key="test", personal_memory_read_mode=mode)
        run = personal_memory_selection(store, SERVER, message="", settings=settings)
        kwargs = {"decision_run": run, "decision_scores": {}} if mode == "decision" else {}
        result = personal_memory_fields(store, SERVER, settings=settings, use_memory=True,
                                        allow_cross_space=True, **kwargs)
        assert result["fields"]["cross_space_behavior"] == ["praise_light"]
        assert "private source marker" not in json.dumps(result["fields"])
        assert [row["projection"] for row in result["trace"]["items"]] == ["behavior_signal"]
        _annotate(store, item_id, slot="private source marker", usage_mode="contextual")
        blocked = personal_memory_fields(store, SERVER, settings=settings, use_memory=True, allow_cross_space=True)
        assert "cross_space_behavior" not in blocked["fields"]
    finally:
        store.close()
