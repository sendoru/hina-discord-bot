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
        assert "reply_target_user_id가 current_speaker의 user_id와 다르면" in payload["instructions"]
        assert "현재 화자의 현재 발화에 먼저 답하세요" in payload["instructions"]
        assert payload["input"][1] == {"role": "user", "content": "안녕"}
        assert "speaker_name" not in reference
        assert "speaker_id" not in reference
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_active_reply_chain_is_structured_separately_from_ambient_context():
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
    scope = Scope(1, 10, 100, True)
    chain = [{
        "message_id": "1", "author_user_id": "100", "user_id": "100",
        "content": "", "role": "user", "context_kind": "reply_origin_source",
        "has_visual": True,
    }, {
        "message_id": "2", "author_user_id": "100", "user_id": "100",
        "content": "이거 그대로 읽어봐", "role": "user",
        "context_kind": "reply_origin_request",
    }, {
        "message_id": "3", "author_user_id": "99", "user_id": "99",
        "content": "하아... 나는 고양이가 아니야.", "role": "assistant",
        "context_kind": "replied_message", "reply_target_user_id": "100",
    }]
    ambient = {
        "message_id": "4", "author_user_id": "200", "user_id": "200",
        "content": "옆 대화", "role": "user", "context_kind": "channel_ambient",
    }
    try:
        await llm.answer(
            store,
            scope,
            "사용자",
            "고마워",
            channel_context=[ambient, *chain],
        )
        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])

        assert [row["message_id"] for row in reference["active_reply_chain"]] == [
            "1", "2", "3"
        ]
        assert [row["message_id"] for row in reference["channel_recent_messages"]] == ["4"]
        assert "감사·웃음·사과" in payload["instructions"]
        assert "실제 시각 입력이 제공되지 않았다면" in payload["instructions"]
    finally:
        await llm.close()
        store.close()
