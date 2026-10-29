"""Selective-memory privacy, ranking, local tool and answer integration."""

import json
from dataclasses import replace

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.local_tools import LocalToolCall, LocalToolRegistry, LocalToolSpec
from hina_bot.ai.runtime_llm import LLM
from hina_bot.ai.selective_memory import (
    memory_tool_registry,
    profile_items,
    project_selective_context,
    search_memory_items,
)
from hina_bot.ai.structured_memory_context import structured_memory_context
from hina_bot.core.config import Settings
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def add(store, scope, text, *, kind=MemoryKind.FACT, disclosure=MemoryDisclosure.LOCAL):
    return store.add_memory_item(
        scope, text, kind=kind, disclosure=disclosure, confidence=0.95,
    )


def test_profile_is_bounded_prioritizes_stable_preferences_and_does_not_force_events():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        add(store, scope, "한 달 전 일회성 프로젝트 장애", kind=MemoryKind.EVENT)
        add(store, scope, "답변은 간결하게", kind=MemoryKind.PREFERENCE)
        add(store, scope, "해당 주제는 먼저 꺼내지 말기", kind=MemoryKind.BOUNDARY)
        for i in range(20):
            add(store, scope, f"지속적인 작업 환경 {i}")
        selected = profile_items(store.memory_items(100), scope)
        assert len(selected) <= 5
        assert sum(len(item.content) for item in selected) <= 1400
        assert any(item.kind == MemoryKind.BOUNDARY for item in selected)
        assert any(item.kind == MemoryKind.PREFERENCE for item in selected)
        assert all(item.kind != MemoryKind.EVENT for item in selected)
        legacy = structured_memory_context(
            store, scope, use_memory=True, allow_cross_space=True,
        )
        context, ids = project_selective_context(legacy, store.memory_items(100), scope)
        assert len(context["structured_owner_memory"]) == len(ids)
        assert "한 달 전" not in json.dumps(context, ensure_ascii=False)
    finally:
        store.close()


def test_fts_search_enforces_owner_and_channel_privacy_before_ranking():
    store = Store.create(":memory:")
    target = Scope(1, 90, 100, True)
    same_public = Scope(1, 10, 100, True)
    same_private = Scope(1, 11, 100, False)
    other_server = Scope(2, 12, 100, True)
    other_user = Scope(1, 10, 200, True)
    try:
        public_id = add(store, same_public, "로봇 프로젝트 자율주행 카메라")
        add(store, same_private, "로봇 비공개 채널의 비밀 기록")
        add(store, other_server, "로봇 다른 서버의 개인 기록")
        add(store, other_server, "로봇 타 서버 공개 허용", disclosure=MemoryDisclosure.GLOBAL)
        add(store, other_server, "로봇 타 서버 명시적 참조 필요", disclosure=MemoryDisclosure.REFERENCE_GATED)
        add(store, other_user, "로봇 다른 사람의 비밀", disclosure=MemoryDisclosure.GLOBAL)
        results = search_memory_items(store.memory_items(100), target, "로봇")
        texts = [item["content"] for item in results]
        assert any(item["id"] == public_id for item in results)
        assert "로봇 타 서버 공개 허용" in texts
        assert "로봇 비공개 채널의 비밀 기록" not in texts
        assert "로봇 다른 서버의 개인 기록" not in texts
        assert "로봇 타 서버 명시적 참조 필요" not in texts
        assert "로봇 다른 사람의 비밀" not in texts
        dm_results = search_memory_items(store.memory_items(100), Scope(None, 55, 100), "비공개")
        assert "로봇 비공개 채널의 비밀 기록" in [x["content"] for x in dm_results]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_search_tool_is_per_request_bounded_and_reflects_retraction():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        item_id = add(store, scope, "프로젝트 로봇 내비게이션", kind=MemoryKind.TASK)
        original = LocalToolRegistry()
        original.register(
            LocalToolSpec("echo", "echo", {"type": "object", "properties": {}}),
            lambda _args: {"ok": True},
        )
        registry = memory_tool_registry(original, store, scope)
        assert len(original.schemas()) == 1
        assert len(registry.schemas()) == 2
        first = await registry.execute(LocalToolCall("1", "search_memory", {"query": "로봇"}))
        assert not first.is_error
        assert first.output["items"][0]["id"] == item_id
        store.retract_memory_item(item_id, expected_revision=0)
        second = await registry.execute(LocalToolCall("2", "search_memory", {"query": "로봇"}))
        assert second.output["count"] == 0
        third = await registry.execute(LocalToolCall("3", "search_memory", {"query": "로봇"}))
        assert third.is_error
        assert third.output == {"error": "search_limit_exceeded"}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_answer_mode_switch_changes_projection_and_exposes_tool(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "test", "status": "completed", "model": "gpt-4.1-mini",
            "output": [{
                "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "알겠어", "annotations": []}],
            }],
        })

    client = AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    add(store, scope, "계속 사용하는 편집기는 Neovim", kind=MemoryKind.PREFERENCE)
    add(store, scope, "옛날 특정 로봇 프로젝트 문제", kind=MemoryKind.EVENT)
    llm = LLM(
        Settings(
            discord_token="test", openai_api_key="test",
            chat_web_search=False, personal_memory_read_mode="selective",
            usage_log_path=str(tmp_path / "usage.jsonl"),
        ),
        client,
    )
    try:
        await llm.answer(store, scope, "User", "안녕")
        selective = calls[-1]
        assert "search_memory" in [tool.get("name") for tool in selective["tools"]]
        assert "Neovim" in json.dumps(selective["input"], ensure_ascii=False)
        assert "옛날 특정" not in json.dumps(selective["input"], ensure_ascii=False)

        llm.settings = replace(llm.settings, personal_memory_read_mode="legacy")
        await llm.answer(store, scope, "User", "안녕")
        legacy = calls[-1]
        assert "search_memory" not in [tool.get("name") for tool in legacy.get("tools", [])]
        assert "옛날 특정" in json.dumps(legacy["input"], ensure_ascii=False)
    finally:
        store.close()
        await llm.close()
