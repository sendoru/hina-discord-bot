import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import LLM
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


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
    llm = LLM(Settings("test", "test", external_context_policy="full"), client=client)
    store = Store(":memory:")
    scope = Scope(1, 10, 222, True)
    channel_context = [{
        "message_id": "100",
        "user_id": "111",
        "author_user_id": "111",
        "name": "sendol",
        "content": "과거 메시지",
        "role": "user",
    }, {
        "message_id": "101",
        "user_id": "222",
        "author_user_id": "222",
        "name": "titn",
        "content": "현재 화자의 이전 메시지",
        "role": "user",
    }]
    try:
        await llm.answer(
            store,
            scope,
            "titn",
            "안녕",
            channel_context=channel_context,
        )

        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])

        assert reference["current_speaker"] == {
            "user_id": "222",
            "name": "titn",
            "relation": "author_of_following_user_message",
        }
        other, current = reference["channel_recent_messages"]
        assert other["user_id"] == "111"
        assert other["name"] == "sendol"
        assert other["is_current_speaker"] is False
        assert current["user_id"] == "222"
        assert current["name"] == "titn"
        assert current["is_current_speaker"] is True
        assert "is_current_speaker" not in channel_context[0]
        assert "현재 사용자에게 직접 말을 걸거나" in payload["instructions"]
        assert "제3자로 지칭" in payload["instructions"]
        assert payload["input"][1] == {"role": "user", "content": "안녕"}
        assert "speaker_name" not in reference
        assert "speaker_id" not in reference
    finally:
        await llm.close()
        store.close()
