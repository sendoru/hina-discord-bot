"""Ensure DM/guild space instructions are selected independently of relationship mode."""

import json
from importlib.resources import files

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.request_assembly import DM_SPACE_POLICY, GUILD_SPACE_POLICY
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
@pytest.mark.parametrize(
    ("scope", "is_dm", "is_special"),
    [
        (Scope(None, 10, 200), True, False),
        (Scope(None, 10, 100), True, True),
        (Scope(1, 10, 100, True), False, False),
    ],
    ids=["ordinary-dm", "special-dm", "guild"],
)
async def test_answer_uses_exactly_one_space_prompt_without_changing_relationship(
    scope, is_dm, is_special,
):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(
        Settings(
            discord_token="test",
            openai_api_key="test",
            special_dm_user_id=100,
            chat_web_search=False,
        ),
        client=client,
    )
    store = Store.create(":memory:")
    try:
        await llm.answer(store, scope, "사용자", "안녕", use_memory=False)
        instructions = calls[-1]["instructions"]
        ordinary = files("hina_bot").joinpath(
            "prompts/ordinary_relationship.md"
        ).read_text(encoding="utf-8").strip()
        special = files("hina_bot").joinpath(
            "prompts/special_dm.md"
        ).read_text(encoding="utf-8").strip()

        assert (DM_SPACE_POLICY.strip() in instructions) is is_dm
        assert (GUILD_SPACE_POLICY.strip() in instructions) is not is_dm
        assert (special in instructions) is is_special
        assert (ordinary in instructions) is not is_special
        assert llm.character in instructions
        assert ("자동 응답 채널에서는" in instructions) is not is_dm
    finally:
        await llm.close()
        store.close()
