import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.runtime_llm import LLM
from hina_bot.config import Settings
from hina_bot.routing import Scope
from hina_bot.store import Store


@pytest.mark.asyncio
async def test_direct_party_only_filters_provider_payload_even_if_broad_context_is_supplied():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
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
                "content": [{"type": "output_text", "text": "응.", "annotations": []}],
            }],
        })

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings(
        "test",
        "test",
        model="test-model",
        usage_log_path="",
        chat_web_search=False,
        external_context_policy="direct_party_only",
    ), client=client)
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    store.set_note(scope.realm, "SERVER-NOTE-SECRET")
    store.set_note(scope.user_note, "MY-NOTE")
    store.add(scope, 1, "my-old-direct", "hina-old-reply")
    store.save_summary(scope, "MY-SUMMARY", 1)

    channel_context = [
        {
            "message_id": "10",
            "role": "user",
            "user_id": "100",
            "author_user_id": "100",
            "direct_trigger": True,
            "content": "MY-DIRECT",
        },
        {
            "message_id": "11",
            "role": "user",
            "user_id": "100",
            "author_user_id": "100",
            "direct_trigger": False,
            "content": "MY-AMBIENT",
        },
        {
            "message_id": "12",
            "role": "user",
            "user_id": "200",
            "author_user_id": "200",
            "direct_trigger": True,
            "content": "OTHER-DIRECT",
        },
        {
            "message_id": "13",
            "role": "assistant",
            "reply_target_user_id": "100",
            "content": "HINA-TO-ME",
        },
        {
            "message_id": "14",
            "role": "assistant",
            "reply_target_user_id": "200",
            "content": "HINA-TO-OTHER",
        },
        {
            "message_id": "15",
            "role": "user",
            "user_id": "200",
            "author_user_id": "200",
            "direct_trigger": True,
            "context_kind": "target_user_history",
            "content": "TARGET-HISTORY",
        },
    ]
    public_context = [
        {
            "source": "guild:1:channel:20:user:100",
            "user_id": "100",
            "summary": "MY-PUBLIC-MEMORY",
        },
        {
            "source": "guild:1:channel:20:user:200",
            "user_id": "200",
            "summary": "OTHER-PUBLIC-MEMORY",
        },
    ]

    try:
        await llm.answer(
            store,
            scope,
            "현재 사용자",
            "히나야 지금 뭐해?",
            public_context=public_context,
            channel_context=channel_context,
        )
        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])

        serialized = json.dumps(reference, ensure_ascii=False)
        assert "MY-DIRECT" in serialized
        assert "HINA-TO-ME" in serialized
        assert "MY-NOTE" in serialized
        assert "MY-SUMMARY" in serialized
        assert "MY-PUBLIC-MEMORY" in serialized

        for blocked in (
            "SERVER-NOTE-SECRET",
            "MY-AMBIENT",
            "OTHER-DIRECT",
            "HINA-TO-OTHER",
            "TARGET-HISTORY",
            "OTHER-PUBLIC-MEMORY",
        ):
            assert blocked not in serialized
        assert payload["input"][-1]["content"] == "히나야 지금 뭐해?"
    finally:
        await llm.close()
        store.close()
