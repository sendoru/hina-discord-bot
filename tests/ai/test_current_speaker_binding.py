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
async def test_current_speaker_is_explicitly_bound_to_visible_user_turn():
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
    scope = Scope(1, 10, 222, True)
    try:
        await llm.answer(
            store,
            scope,
            "titn",
            "안녕",
            channel_context=[{
                "message_id": "100",
                "user_id": "111",
                "author_user_id": "111",
                "name": "sendol",
                "content": "과거 메시지",
                "role": "user",
            }],
        )

        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])

        assert reference["current_speaker"] == {
            "user_id": "222",
            "name": "titn",
            "relation": "author_of_following_user_message",
        }
        assert reference["channel_recent_messages"][0]["user_id"] == "111"
        assert reference["channel_recent_messages"][0]["name"] == "sendol"
        assert payload["input"][1] == {"role": "user", "content": "안녕"}
        assert "speaker_name" not in reference
        assert "speaker_id" not in reference
    finally:
        await llm.close()
        store.close()
