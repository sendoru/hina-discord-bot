import json

import httpx
import pytest
from hina_bot.config import Settings
from hina_bot.routing import Scope
from hina_bot.store import Store
from openai import AsyncOpenAI

from hina_bot.ai.contextual_chat_llm import LLM


@pytest.mark.asyncio
async def test_weather_followup_uses_prior_location_but_keeps_visible_message():
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
        chat_web_search=True,
    ), client=client)
    store = Store(":memory:")
    scope = Scope(None, 20, 100)
    store.add(scope, 1, "서울 내일 날씨 어때?", "확인해볼게.")
    try:
        await llm.answer(store, scope, "사용자", "그럼 모레는?")
        payload = calls[-1]
        assert payload["tool_choice"] == "required"
        assert payload["tools"][0]["type"] == "web_search"
        assert payload["input"][-1]["content"] == "그럼 모레는?"
    finally:
        await llm.close()
        store.close()
