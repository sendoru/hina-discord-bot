import json

import httpx
import pytest
from hina_bot.config import Settings
from hina_bot.lore import LoreIndex
from hina_bot.routing import Scope
from hina_bot.store import Store
from openai import AsyncOpenAI

from hina_bot.ai.contextual_chat_llm import LLM


@pytest.mark.asyncio
async def test_lore_followup_uses_previous_entity_but_keeps_visible_message():
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
    llm.lore = LoreIndex([{
        "id": "canon.test.kayoko.hina",
        "lane": "canon",
        "fact_type": "fact_direct",
        "summary": "카요코와 히나는 과거 사건에서 직접 대면한 적이 있다.",
        "keywords": ["카요코", "히나", "만남", "대면"],
        "subjects": ["카요코", "히나"],
        "knowledge": "direct_experience",
        "timeline": "테스트 사건",
    }])
    store = Store(":memory:")
    scope = Scope(None, 20, 100)
    store.add(scope, 1, "카요코가 예전에 뭐 했어?", "예전 이야기를 물어보는 거네.")
    try:
        await llm.answer(store, scope, "사용자", "그럼 걔는 히나랑 만난 적 있어?")
        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        refs = [row.get("reference") for row in reference["lore_reference"]]
        assert "canon.test.kayoko.hina" in refs
        assert payload["input"][-1]["content"] == "그럼 걔는 히나랑 만난 적 있어?"
    finally:
        await llm.close()
        store.close()
