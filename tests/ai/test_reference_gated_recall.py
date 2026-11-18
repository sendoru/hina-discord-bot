import json

import httpx
from openai import AsyncOpenAI

from hina_bot.ai.reference_context_blocks import read_reference_context
from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def _response():
    return {
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
            "content": [{"type": "output_text", "text": "응", "annotations": []}],
        }],
    }


def _client(calls):
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    return AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _reference(call):
    return read_reference_context(call["input"][0]["content"])


async def test_explicit_owner_reference_cannot_unlock_cross_space_memory():
    calls = []
    llm = LLM(
        Settings(discord_token="test", openai_api_key="test",
            model="test-model",
            usage_log_path="",
            chat_web_search=False,
            external_context_policy="full",
        ),
        client=_client(calls),
    )
    store = Store.create(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        store.add_memory_item(
            dm,
            "다음 주 삼성 면접이 있다",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        store.add_memory_item(
            dm,
            "고양이 이름은 하루다",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        await llm.answer(
            store,
            server,
            "사용자",
            "내가 전에 말했던 삼성 면접 있잖아",
        )

        reference = _reference(calls[-1])
        assert not reference.get("authorized_factual_memory")
        assert "다음 주 삼성 면접이 있다" not in json.dumps(reference, ensure_ascii=False)
    finally:
        CURRENT_CONTEXT_PROVENANCE.set(None)
        await llm.close()
        store.close()


