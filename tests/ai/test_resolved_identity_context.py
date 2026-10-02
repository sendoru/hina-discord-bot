import json
from types import SimpleNamespace as NS

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.identity_context import CURRENT_RESOLVED_IDENTITIES
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


@pytest.mark.parametrize("policy", ["full", "bot_interactions_only"])
@pytest.mark.parametrize("memory_mode", ["normal", "off", "write_only"])
async def test_identity_reaches_answer_independently_of_memory_with_final_privacy_filter(
    policy, memory_mode,
):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "status": "completed",
            "created_at": 0, "model": "test",
            "output": [{
                "type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "응", "annotations": []}],
            }],
        })

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(
        Settings(discord_token="test", db_path=":memory:", external_context_policy=policy,
                 chat_web_search=False),
        client=client,
    )
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    store.set_memory_mode_override("global", memory_mode)
    selected = {
        "user_id": "200", "reference": "2_718281", "names": ["CURRENT-NAME", "2_718281"],
    }
    token = CURRENT_RESOLVED_IDENTITIES.set((selected,))
    try:
        await llm.answer(
            store, scope, "요청자", "2_718281 핑해줘", use_memory=memory_mode == "normal",
        )
        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["resolved_identities"] == ([selected] if policy == "full" else [])
        assert reference["current_interaction"]["mentions"] == []
        assert reference["current_speaker"]["user_id"] == "100"
        assert reference["channel_recent_messages"] == []
        assert reference["public_server_context"] == []
        assert "user_id를 그대로 <@user_id>" in payload["instructions"]
        assert "확인되지 않은 이름의 user_id를 추측하지 말고" in payload["instructions"]
        provenance = CURRENT_CONTEXT_PROVENANCE.get()
        identity_section = next(
            row for row in provenance["sections"] if row["name"] == "resolved_identities"
        )
        assert identity_section["count"] == (1 if policy == "full" else 0)
        assert identity_section["blocked_count"] == (0 if policy == "full" else 1)
        assert "CURRENT-NAME" not in json.dumps(provenance)
        assert "2_718281" not in json.dumps(provenance)
    finally:
        CURRENT_RESOLVED_IDENTITIES.reset(token)
        await llm.close()
        store.close()


@pytest.mark.parametrize("policy", ["full", "bot_interactions_only"])
def test_model_routing_counts_the_same_admitted_identity_as_the_answer(policy):
    # No provider request: routing budgets must include the field even with memory disabled.
    llm = InformationPipeline(
        Settings(discord_token="test", db_path=":memory:", external_context_policy=policy),
        client=NS(provider_name="openai"),
    )
    store = Store(":memory:")
    scope = Scope(1, 10, 100)

    def budget():
        return llm._routing_context_chars(
            store, scope, "someone 핑해줘", public_context=[], channel_context=[], use_memory=False,
        )[0]

    try:
        before = budget()
        token = CURRENT_RESOLVED_IDENTITIES.set(({
            "user_id": "200", "reference": "someone", "names": ["someone"],
        },))
        try:
            after = budget()
        finally:
            CURRENT_RESOLVED_IDENTITIES.reset(token)
        assert (after > before) if policy == "full" else (after == before)
    finally:
        llm.admin_db.close()
        llm.usage.close()
        store.close()
