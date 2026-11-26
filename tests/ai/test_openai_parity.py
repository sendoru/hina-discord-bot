import json

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from hina_bot.ai.audio import CURRENT_AUDIO_UNAVAILABLE
from hina_bot.ai.decisions import DecisionsClient
from hina_bot.ai.local_tools import LocalToolExecutor, LocalToolRegistry, LocalToolSpec
from hina_bot.ai.openai_capabilities import reasoning_options
from hina_bot.ai.providers import OpenAIClient
from hina_bot.ai.runtime_llm import LLM
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.runtime_config import RUNTIME_SETTING_SPECS, parse_runtime_value
from hina_bot.core.store import Store


@pytest.mark.parametrize(("text", "reason"), [
    ('{"c1":0.1,"c1":0.3}', "duplicate_response_key"),
    ('{"other":1}', "invalid_candidate_ids"), ('{}', "invalid_candidate_ids"),
    ('{"c1":true}', "invalid_probability"), ('{"c1":1.1}', "invalid_probability"),
    ('{"c1":NaN}', "invalid_probability"),
])
async def test_invalid_openai_selection_fails_closed(text, reason):
    def wire(req):
        assert str(req.url) == "https://api.openai.com/v1/responses"
        assert "x-goog-api-key" not in req.headers
        assert req.headers["Authorization"] == "Bearer fixture"
        body = json.loads(req.content)
        assert body["store"] is False and body["text"]["format"]["strict"]
        assert body["text"]["format"]["schema"]["required"] == ["c1"]
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
        result = await DecisionsClient(http, "openai", "gpt-4.1-mini").evaluate(
            api_key="fixture", domain="memory", candidates={"memory.7": {}}, query={},
        )
    assert result.reason == reason and result.scores is None


def test_openai_runtime_effort_and_model_capability():
    settings = Settings(discord_token="fixture", openai_fast_reasoning_effort="low")
    spec = RUNTIME_SETTING_SPECS["openai_fast_reasoning_effort"]
    assert parse_runtime_value(spec, " LOW ", settings=settings) == "low"
    with pytest.raises(ValueError):
        parse_runtime_value(spec, "turbo", settings=settings)
    assert reasoning_options(settings, "gpt-5.4-mini", "fast") == {"reasoning": {"effort": "low"}}
    assert reasoning_options(settings, "gpt-4.1-mini", "fast") == {}
    assert reasoning_options(settings, "gpt-5.4-mini", "smart") == {}


async def test_stateless_sdk_tool_loop_and_token_accounting(tmp_path):
    sent = []
    calls = [{"type": "function_call", "name": "echo", "arguments": "{}",
              "id": f"fc_{i}", "call_id": f"call_{i}"} for i in range(2)]
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}

    def wire(req):
        body = json.loads(req.content)
        sent.append(body)
        assert body["store"] is False and "previous_response_id" not in body
        return httpx.Response(200, json={
            "id": f"resp_{len(sent)}", "object": "response", "created_at": 1,
            "status": "completed", "model": body["model"],
            "output": [reasoning, *calls] if len(sent) == 1 else [],
            "usage": {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 4},
                      "output_tokens": 3, "output_tokens_details": {"reasoning_tokens": 2},
                      "total_tokens": 13},
        })

    registry = LocalToolRegistry()
    registry.register(LocalToolSpec("echo", "Echo", {"type": "object", "properties": {}}), lambda _: {})
    path = tmp_path / "usage.jsonl"
    usage = UsageLogger(str(path))
    async with OpenAIClient(api_key="fixture", http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(wire),
    )) as client:
        async def send(request):
            return await usage.request(client, "answer", **request)

        try:
            await LocalToolExecutor(registry).run(send, {
                "model": "gpt-5.4-mini", "store": False, "input": "hello",
                "tools": registry.schemas(), "reasoning": {"effort": "low"},
            }, provider="openai")
        finally:
            usage.close()
    assert len(sent) == 2
    assert sent[1]["input"][1:4] == [reasoning, *calls]
    assert [item["call_id"] for item in sent[1]["input"][4:]] == ["call_0", "call_1"]
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2
    assert all((r["provider"], r["cached_tokens"], r["reasoning_tokens"],
                r["requested_reasoning_effort"]) == ("openai", 4, 2, "low") for r in rows)

@pytest.mark.parametrize(("failure", "attempts"), [("timeout", 1), (400, 1), (503, 2)])
async def test_answer_error_budget_and_audio_wire(failure, attempts):
    requests = []

    def wire(req):
        body = json.loads(req.content)
        requests.append(body)
        assert body["store"] is False and body["reasoning"] == {"effort": "low"}
        assert not any(block.get("type") == "input_audio" for item in body["input"]
                       if isinstance(item.get("content"), list) for block in item["content"])
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture", request=req)
        return httpx.Response(failure, json={"error": {"message": "synthetic"}})

    client = OpenAIClient(api_key="fixture", max_retries=0, http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(wire),
    ))
    llm = LLM(Settings(discord_token="fixture", model="gpt-5.4-mini", openai_reasoning_effort="low"),
              client=client)
    store = Store.create(":memory:")
    token = CURRENT_AUDIO_UNAVAILABLE.set(True)
    try:
        with pytest.raises(APITimeoutError if failure == "timeout" else APIStatusError):
            await llm.answer(store, Scope(None, 1, 2), "test", "hello")
    finally:
        CURRENT_AUDIO_UNAVAILABLE.reset(token)
        await llm.close()
        store.close()
    assert len(requests) == attempts
