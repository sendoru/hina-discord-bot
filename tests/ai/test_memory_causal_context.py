import json
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from hina_bot.ai.memory_summary import SUMMARY_POLICY, MemorySummaryMixin
from hina_bot.core.memory_context import (
    CURRENT_MEMORY_CONTEXT,
    build_memory_context,
    decode_memory_context,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_memory_context_keeps_only_strong_causal_rows_with_bounded_ownership():
    rows = [
        {
            "context_kind": "channel_ambient",
            "role": "user",
            "author_user_id": "300",
            "content": "unrelated",
        },
        {
            "context_kind": "reply_origin_source",
            "role": "user",
            "author_user_id": "200",
            "content": "나는 매운 음식을 못 먹어",
        },
        {
            "context_kind": "reply_origin_request",
            "role": "user",
            "author_user_id": "100",
            "content": "이 사람 말이랑 비슷한 사례가 있어?",
        },
        {
            "context_kind": "replied_message",
            "role": "assistant",
            "author_user_id": "999",
            "content": "이런 취향을 말하는 거야.",
        },
    ]

    context = build_memory_context(rows, 100)

    assert context == [
        {
            "kind": "replied_message",
            "role": "assistant",
            "ownership": "assistant",
            "content": "이런 취향을 말하는 거야.",
        },
        {
            "kind": "reply_origin_request",
            "role": "user",
            "ownership": "self",
            "content": "이 사람 말이랑 비슷한 사례가 있어?",
        },
    ]
    assert sum(len(item["content"]) for item in context) <= 1600


def test_store_consumes_request_scoped_memory_context_once():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    context = ({
        "kind": "replied_message",
        "role": "user",
        "ownership": "external",
        "content": "난 이런 옷 좋아해",
    },)
    token = CURRENT_MEMORY_CONTEXT.set(context)
    try:
        store.add(scope, 1, "히나야 나도 이거 좋아해", "그렇구나")
        store.add(scope, 2, "히나야 그리고 이것도", "응")
        rows = store.pending(scope)
    finally:
        CURRENT_MEMORY_CONTEXT.reset(token)
        store.close()

    assert decode_memory_context(rows[0]["memory_context"]) == list(context)
    assert rows[1]["memory_context"] == ""


def test_store_migrates_existing_turns_table_with_memory_context(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    try:
        db.execute(
            "CREATE TABLE turns ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, realm TEXT NOT NULL, "
            "user_id TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE, content TEXT NOT NULL, "
            "reply TEXT NOT NULL, exportable INTEGER NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        db.commit()
    finally:
        db.close()

    store = Store(str(path))
    try:
        columns = {row["name"] for row in store.db.execute("PRAGMA table_info(turns)")}
        assert "memory_context" in columns
    finally:
        store.close()


def _summary_mixin(output_text="갱신된 기억"):
    mixin = object.__new__(MemorySummaryMixin)
    mixin.settings = NS(
        summary_every=1,
        model="memory-model",
        model_routing_mode="fixed",
        memory_routing_smart_threshold=2.0,
        memory_output_tokens=4096,
        gemini_thinking_level="low",
        provider="openai",
    )
    mixin.client = object()
    mixin.usage = NS(
        request=AsyncMock(return_value=NS(status="completed", output_text=output_text)),
        routing_event=Mock(),
    )
    return mixin


@pytest.mark.asyncio
async def test_personal_summary_receives_causal_context_and_logs_size_telemetry():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    mixin = _summary_mixin()
    context = [{
        "kind": "replied_message",
        "role": "user",
        "ownership": "external",
        "content": "난 매운 음식을 못 먹어",
    }]
    try:
        store.add(
            scope,
            1,
            "히나야 나도 그래",
            "그렇구나",
            memory_context=context,
        )
        await mixin.summarize(store, scope)
    finally:
        store.close()

    summary_call = next(
        call for call in mixin.usage.request.await_args_list
        if call.args[1] == "summarize"
    )
    request = summary_call.kwargs
    payload = json.loads(request["input"])
    assert payload["new_turns"] == [{
        "at": payload["new_turns"][0]["at"],
        "user": "히나야 나도 그래",
        "context": context,
    }]
    assert "hina" not in payload["new_turns"][0]
    assert "해석하기 위한 제한된 인과 문맥" in SUMMARY_POLICY

    event = next(
        call for call in mixin.usage.routing_event.call_args_list
        if call.args == ("memory.summary_requested",)
    )
    assert event.kwargs["status"] == "requested"
    assert event.kwargs["memory_kind"] == "personal"
    assert event.kwargs["pending_turns"] == 1
    assert event.kwargs["batch_turns"] == 1
    assert event.kwargs["context_items"] == 1
    assert event.kwargs["old_memory_chars"] == 0
    assert event.kwargs["payload_chars"] == len(request["input"])