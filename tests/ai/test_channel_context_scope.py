import json

import httpx
import pytest
from hina_bot.chat_llm import LLM
from hina_bot.config import Settings
from hina_bot.routing import Scope
from hina_bot.store import Store
from openai import AsyncOpenAI


def _response():
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "gpt-4.1-mini",
        "output": [{
            "type": "message",
            "id": "msg_test",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "응", "annotations": []}],
        }],
    }


@pytest.mark.asyncio
async def test_current_channel_query_excludes_cross_channel_memory():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings("test", "test"), client=client)
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    try:
        store.set_note(scope.realm, "OTHER_CHANNEL_SERVER_NOTE")
        store.set_note(scope.user_note, "OTHER_CHANNEL_USER_NOTE")
        store.add(scope, 1, "CURRENT_CHANNEL_PERSONAL", "current reply")

        await llm.answer(
            store,
            scope,
            "사용자",
            "이 채널에서 어떤 대화가 오갔는지 요약해 줘",
            public_context=[{
                "source": "guild:1:channel:20:user:200",
                "summary": "OTHER_CHANNEL_PUBLIC_CONTEXT",
            }],
            channel_context=[{
                "message_id": "20",
                "user_id": "200",
                "name": "다른 사용자",
                "content": "CURRENT_CHANNEL_RECENT",
                "role": "user",
            }],
        )

        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["server_note"] == ""
        assert reference["user_note"] == ""
        assert reference["public_server_context"] == []
        assert reference["channel_recent_messages"][0]["content"] == "CURRENT_CHANNEL_RECENT"
        assert "CURRENT_CHANNEL_PERSONAL" in json.dumps(
            reference["personal_recent_conversation"], ensure_ascii=False
        )
        assert "현재 채널 범위" in payload["instructions"]
        assert "OTHER_CHANNEL" not in json.dumps(reference, ensure_ascii=False)
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_server_query_keeps_authorized_cross_channel_context():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings("test", "test"), client=client)
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    try:
        store.set_note(scope.realm, "SERVER_NOTE_MARKER")
        store.set_note(scope.user_note, "USER_NOTE_MARKER")
        await llm.answer(
            store,
            scope,
            "사용자",
            "이 서버에서 최근 무슨 얘기가 있었어?",
            public_context=[{
                "source": "guild:1:channel:20:user:200",
                "summary": "SERVER_PUBLIC_CONTEXT",
            }],
            channel_context=[],
        )

        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["server_note"] == "SERVER_NOTE_MARKER"
        assert reference["user_note"] == "USER_NOTE_MARKER"
        assert reference["public_server_context"][0]["summary"] == "SERVER_PUBLIC_CONTEXT"
        assert "현재 채널 범위" not in payload["instructions"]
    finally:
        await llm.close()
        store.close()


def test_channel_scope_classifier_does_not_match_unrelated_word_prefixes():
    scope = Scope(1, 10, 100)
    assert LLM._current_channel_scope_only(scope, "이 채널에서 뭐 했어?")
    assert LLM._current_channel_scope_only(scope, "현재 방의 대화를 요약해 줘")
    assert not LLM._current_channel_scope_only(scope, "이 방식으로 하면 돼?")
    assert not LLM._current_channel_scope_only(scope, "이 서버에서 무슨 얘기했어?")
