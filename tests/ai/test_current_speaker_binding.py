import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import LLM
from hina_bot.core.config import Settings
from hina_bot.core.interaction_context import CURRENT_INTERACTION_CONTEXT
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
        "user_id": "99",
        "author_user_id": "99",
        "reply_target_user_id": "111",
        "name": "히나",
        "content": "다른 사용자에게 한 답변",
        "role": "assistant",
    }, {
        "message_id": "102",
        "user_id": "222",
        "author_user_id": "222",
        "name": "titn",
        "content": "현재 화자의 이전 메시지",
        "role": "user",
    }, {
        "message_id": "103",
        "user_id": "99",
        "author_user_id": "99",
        "reply_target_user_id": "222",
        "name": "히나",
        "content": "현재 사용자에게 한 답변",
        "role": "assistant",
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
        other, other_reply, current, current_reply = reference["channel_recent_messages"]
        assert other["user_id"] == "111"
        assert other["name"] == "sendol"
        assert other["is_current_speaker"] is False
        assert other_reply["reply_target_user_id"] == "111"
        assert other_reply["reply_target_is_current_speaker"] is False
        assert current["user_id"] == "222"
        assert current["name"] == "titn"
        assert current["is_current_speaker"] is True
        assert current_reply["reply_target_user_id"] == "222"
        assert current_reply["reply_target_is_current_speaker"] is True
        assert "is_current_speaker" not in channel_context[0]
        assert "reply_target_is_current_speaker" not in channel_context[1]
        assert "현재 사용자에게 직접 말을 걸거나" in payload["instructions"]
        assert "제3자로 지칭" in payload["instructions"]
        assert "reply_target_is_current_speaker" in payload["instructions"]
        assert "'아까 네가'" in payload["instructions"]
        assert "다른 사람의 channel_ambient 발언" in payload["instructions"]
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


@pytest.mark.asyncio
async def test_other_users_taunt_cannot_become_current_speakers_personal_continuity_evidence():
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
        "message_id": "200",
        "user_id": "111",
        "author_user_id": "111",
        "name": "tjrn",
        "content": "히나야, 네 머리는 정말 작아.",
        "role": "user",
        "context_kind": "channel_ambient",
    }, {
        "message_id": "201",
        "user_id": "99",
        "author_user_id": "99",
        "reply_target_user_id": "111",
        "name": "히나",
        "content": "갑자기 그런 소릴 해서...",
        "role": "assistant",
        "context_kind": "channel_ambient",
    }]
    try:
        await llm.answer(
            store,
            scope,
            "canhy1557",
            "ㅠㅠㅠㅠㅠ",
            channel_context=channel_context,
        )

        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        taunt, reply = reference["channel_recent_messages"]

        assert taunt["is_current_speaker"] is False
        assert reply["reply_target_is_current_speaker"] is False
        assert reference["current_speaker"]["user_id"] == "222"
        assert "개인\n연속성 표현" in payload["instructions"]
        assert "주변 대화의 흐름을 이해" in payload["instructions"]
        assert "현재 화자가 같은 행동을\n했다고 말하지 마세요" in payload["instructions"]
    finally:
        await llm.close()
        store.close()

@pytest.mark.asyncio
async def test_current_interaction_metadata_reaches_existing_answer_request():
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
    interaction = {
        "speaker": {
            "user_id": "100",
            "name": "사용자",
            "is_bot": False,
            "is_self": False,
        },
        "mentions": [{
            "user_id": "200",
            "name": "리오",
            "is_bot": True,
            "is_self": False,
        }, {
            "user_id": "99",
            "name": "히나",
            "is_bot": True,
            "is_self": True,
        }],
        "reply_target": {
            "user_id": "300",
            "name": "아리스",
            "role": "bot",
            "is_self": False,
        },
    }
    token = CURRENT_INTERACTION_CONTEXT.set(interaction)
    try:
        await llm.answer(
            store,
            scope,
            "사용자",
            "<@200>야, <@99> 좀 쓰다듬어줘",
        )
        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])

        assert reference["current_interaction"] == interaction
        assert "mention되었다는 사실만으로" in payload["instructions"]
        assert "요청이 반드시 히나에게 향한 것은" in payload["instructions"]
        assert "문장의 호격 표현" in payload["instructions"]
        assert "reply_target도 강한 대화 연결 신호" in payload["instructions"]
        assert payload["input"][1]["content"] == "<@200>야, <@99> 좀 쓰다듬어줘"
    finally:
        CURRENT_INTERACTION_CONTEXT.reset(token)
        await llm.close()
        store.close()

@pytest.mark.asyncio
async def test_plain_explicit_reply_is_always_split_into_active_reply_chain():
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
    channel_context = [{
        "message_id": "10",
        "author_user_id": "200",
        "user_id": "200",
        "content": "파란색이 좋아",
        "role": "user",
        "context_kind": "replied_message",
        "reference_strength": "explicit_reply",
    }, {
        "message_id": "11",
        "author_user_id": "300",
        "user_id": "300",
        "content": "CUDA 얘기",
        "role": "user",
        "context_kind": "channel_ambient",
    }]
    try:
        await llm.answer(
            store,
            scope,
            "사용자",
            "왜?",
            channel_context=channel_context,
        )
        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])

        assert [row["message_id"] for row in reference["active_reply_chain"]] == ["10"]
        assert [row["message_id"] for row in reference["channel_recent_messages"]] == ["11"]
        assert (
            "active_reply_chain > speaker_thread > target_user_history > channel_ambient"
            in payload["instructions"]
        )
        assert "새 주제나 새 대상을 명시하면 현재 발화가 가장 우선" in payload["instructions"]
    finally:
        await llm.close()
        store.close()

