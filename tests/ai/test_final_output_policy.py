import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.request_assembly import FINAL_OUTPUT_CHECK_POLICY
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
async def test_final_output_check_is_last_even_after_dynamic_instruction():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings("test", "test"), client=client)
    llm.instructions.active_text = lambda: "[관리자 동적 캐릭터 조정]\n- [test] 동적 지침"
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    try:
        await llm.answer(store, scope, "사용자", "이 이미지나 스티커를 봐줘.")

        instructions = calls[-1]["instructions"]
        assert "[관리자 동적 캐릭터 조정]" in instructions
        assert instructions.endswith(FINAL_OUTPUT_CHECK_POLICY)
        assert "현재 사용자에게 직접 말하는 히나의 대사" in FINAL_OUTPUT_CHECK_POLICY
        assert "관찰·분석 문체를 그대로 출력하지 마세요" in FINAL_OUTPUT_CHECK_POLICY
        assert "객관적인 이미지 설명을 명시적으로 요청하지" in FINAL_OUTPUT_CHECK_POLICY
        assert "'일러스트', '장면', '~하는 모습'" in FINAL_OUTPUT_CHECK_POLICY
    finally:
        await llm.close()
        store.close()
