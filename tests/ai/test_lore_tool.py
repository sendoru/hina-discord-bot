"""search_lore function tool and answer pipeline integration without network calls."""

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.local_tools import (
    CURRENT_LOCAL_TOOLS,
    LocalToolCall,
    LocalToolRegistry,
    LocalToolSpec,
)
from hina_bot.ai.lore_tool import attach_lore_search
from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate
from hina_bot.core.lore_tool_search import LoreKeywordIndex
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def index():
    row = KnowledgeCandidate(
        candidate_id="canon.records",
        source="static_lore",
        reference="canon.records",
        kind="world_fact",
        content="담당자는 문서와 기록을 정리한다.",
        search_text="담당자는 문서와 기록을 정리한다.",
        subjects=("담당자",),
        keywords=("문서", "기록"),
        awareness="direct_experience",
        time="업무 중",
    )
    return LoreKeywordIndex([row])


@pytest.mark.asyncio
async def test_tool_isolated_registry_validation_and_two_search_limit():
    original = LocalToolRegistry()
    original.register(LocalToolSpec(
        "other_tool", "unrelated tool",
        {"type": "object", "properties": {}},
    ), lambda args: args)
    events = []
    registry = attach_lore_search(
        original, index(), lambda: (),
        include_community=False,
        event=lambda operation, **values: events.append((operation, values)),
    )
    assert [row["name"] for row in original.schemas()] == ["other_tool"]
    assert {row["name"] for row in registry.schemas()} == {"other_tool", "search_lore"}
    invalid = await registry.execute(LocalToolCall("invalid", "search_lore", {"queries": []}))
    assert invalid.is_error
    assert invalid.output == {"error": "invalid_lore_query"}

    first = await registry.execute(LocalToolCall(
        "first", "search_lore", {"queries": ["기록 업무"]},
    ))
    assert not first.is_error
    assert first.output["results"][0]["id"] == "canon.records"
    blocked = await registry.execute(LocalToolCall(
        "third", "search_lore", {"queries": ["기록 업무"]},
    ))
    assert blocked.is_error
    assert blocked.output == {"error": "lore_search_limit_exceeded"}
    assert [event[1]["status"] for event in events] == [
        "invalid_arguments", "ok", "limit_exceeded",
    ]


@pytest.mark.asyncio
async def test_tool_mode_omits_full_lore_and_passes_tool_result_back_to_model(tmp_path):
    client = NS(responses=NS(), close=AsyncMock())
    llm = LLM(Settings(
        discord_token="test", db_path=":memory:",
        provider="openai", model_routing_mode="fixed",
        routing_classifier_mode="off", chat_web_search=False,
        rag_mode="tool", usage_log_path=str(tmp_path / "usage.jsonl"),
    ), client=client)
    llm.lore_keyword_index = index()
    llm.usage.request = AsyncMock(side_effect=[
        NS(status="completed", output_text="", output=[NS(
            type="function_call", id="call-1", call_id="call-1",
            name="search_lore",
            arguments=json.dumps({"queries": ["문서 기록"]}, ensure_ascii=False),
        )]),
        NS(status="completed", output_text="기록을 정리하고 있어.", output=[]),
    ])
    store = Store.create(":memory:")
    existing = LocalToolRegistry()
    existing.register(LocalToolSpec(
        "other_tool", "other",
        {"type": "object", "properties": {}},
    ), lambda _: {})
    context_token = CURRENT_LOCAL_TOOLS.set(existing)
    try:
        answer = await llm.answer(
            store, Scope(None, 20, 100),
            "사용자", "담당자가 무슨 일을 하는지 알려줘", use_memory=False,
        )
        requests = list(llm.usage.request.await_args_list)
    finally:
        CURRENT_LOCAL_TOOLS.reset(context_token)
        await llm.close()
        store.close()

    assert answer == "기록을 정리하고 있어."
    assert len(requests) == 2
    first = requests[0].kwargs
    assert "[검수된 정적 설정 자료" not in first["instructions"]
    assert "search_lore" in first["instructions"]
    assert {tool["name"] for tool in first["tools"] if tool["type"] == "function"} == {
        "other_tool", "search_lore",
    }
    assert [spec["name"] for spec in existing.schemas()] == ["other_tool"]
    output = json.loads(requests[1].kwargs["input"][-1]["output"])
    assert output["ok"] is True
    assert output["result"]["results"][0]["id"] == "canon.records"
    events = [
        json.loads(line)
        for line in (tmp_path / "usage.jsonl").read_text().splitlines()
    ]
    assert any(row.get("operation") == "rag.tool"
               and row.get("rag_tool_result_ids") == ["canon.records"]
               for row in events)
    assert any(row.get("operation") == "rag.mode"
               and row.get("rag_selected_context") == "tool" for row in events)
    assert not any(row.get("operation") == "rag.full" for row in events)


@pytest.mark.asyncio
async def test_tool_mode_always_logs_no_call_and_never_injects_static_lore(tmp_path):
    client = NS(responses=NS(), close=AsyncMock())
    llm = LLM(Settings(
        discord_token="test", db_path=":memory:",
        provider="openai", model_routing_mode="fixed",
        routing_classifier_mode="off", chat_web_search=False,
        rag_mode="tool", usage_log_path=str(tmp_path / "usage.jsonl"),
    ), client=client)
    llm.usage.request = AsyncMock(return_value=NS(
        status="completed", output_text="응.", output=[],
    ))
    store = Store.create(":memory:")
    try:
        answer = await llm.answer(
            store, Scope(None, 20, 100), "사용자", "안녕", use_memory=False,
        )
        request = llm.usage.request.await_args.kwargs
    finally:
        await llm.close()
        store.close()

    assert answer == "응."
    assert "[검수된 정적 설정 자료" not in request["instructions"]
    assert any(row["name"] == "search_lore"
               for row in request["tools"] if row["type"] == "function")
    rows = [
        json.loads(line) for line in
        (tmp_path / "usage.jsonl").read_text().splitlines()
    ]
    summaries = [row for row in rows if row.get("operation") == "rag.tool.turn"]
    assert len(summaries) == 1
    assert summaries[0]["status"] == "not_called"
    assert summaries[0]["rag_tool_turn_calls"] == 0


@pytest.mark.asyncio
async def test_tool_turn_summary_counts_retry_and_empty_result(tmp_path):
    events = []
    registry = attach_lore_search(
        None, index(), lambda: (),
        include_community=False,
        event=lambda operation, **values: events.append((operation, values)),
    )
    first = await registry.execute(LocalToolCall(
        "a", "search_lore", {"queries": ["관련 없는 검색어"]},
    ))
    second = await registry.execute(LocalToolCall(
        "b", "search_lore", {"queries": ["문서 기록"]},
    ))
    assert first.output["status"] == "not_found"
    assert second.output["status"] == "ok"
    assert [e[1]["status"] for e in events] == ["not_found", "ok"]


@pytest.mark.asyncio
async def test_provider_failure_is_not_mistaken_for_tool_nonuse(tmp_path):
    client = NS(responses=NS(), close=AsyncMock())
    llm = LLM(Settings(
        discord_token="test", db_path=":memory:",
        provider="openai", model_routing_mode="fixed",
        routing_classifier_mode="off", chat_web_search=False,
        rag_mode="tool", usage_log_path=str(tmp_path / "usage.jsonl"),
    ), client=client)
    llm.usage.request = AsyncMock(side_effect=RuntimeError("provider error"))
    store = Store.create(":memory:")
    try:
        with pytest.raises(RuntimeError, match="provider error"):
            await llm.answer(
                store, Scope(None, 20, 100), "사용자", "관계 알려줘", use_memory=False,
            )
    finally:
        await llm.close()
        store.close()
    rows = [
        json.loads(line) for line in
        (tmp_path / "usage.jsonl").read_text().splitlines()
    ]
    summaries = [row for row in rows if row.get("operation") == "rag.tool.turn"]
    assert len(summaries) == 1
    assert summaries[0]["status"] == "request_error"
