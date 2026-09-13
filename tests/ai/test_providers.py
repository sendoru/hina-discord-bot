from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest

from hina_bot.ai.providers import (
    ProviderAPIError,
    _gemini_input,
    _GeminiResponses,
    _OpenRouterResponses,
    normalize_provider,
)


def test_normalize_provider():
    assert normalize_provider(" Gemini ") == "gemini"
    with pytest.raises(ValueError):
        normalize_provider("unknown")


def test_gemini_input_preserves_conversation_roles():
    value = _gemini_input([
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "첫 답변"},
        {"role": "user", "content": "다음 질문"},
    ])
    assert [step["type"] for step in value] == ["user_input", "model_output", "user_input"]
    assert value[1]["content"][0]["text"] == "첫 답변"


@pytest.mark.asyncio
async def test_gemini_translates_search_and_normalizes_response():
    seen = {}

    async def handler(request: httpx.Request):
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [
                {"type": "google_search_call", "arguments": {"queries": ["test"]}},
                {"type": "model_output", "content": [{
                    "type": "text",
                    "text": "검색 결과야.",
                    "annotations": [{
                        "type": "url_citation",
                        "url": "https://example.com/source",
                        "title": "Example",
                    }],
                }]},
            ],
            "usage": {
                "total_input_tokens": 10,
                "total_output_tokens": 5,
                "total_thought_tokens": 2,
                "total_cached_tokens": 1,
                "total_tokens": 17,
            },
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(http).create(
            model="gemini-test",
            instructions="system",
            input=[{"role": "user", "content": "질문"}],
            max_output_tokens=200,
            store=False,
            tools=[{"type": "web_search", "search_context_size": "low"}],
            tool_choice="required",
        )
    finally:
        await http.aclose()

    payload = seen["json"]
    assert payload["system_instruction"] == "system"
    assert payload["tools"] == [{"type": "google_search", "search_types": ["web_search"]}]
    assert payload["generation_config"]["tool_choice"] == "any"
    assert payload["generation_config"]["thinking_level"] == "low"
    assert payload["generation_config"]["max_output_tokens"] == 4096
    assert response.status == "completed"
    assert "example.com/source" in response.output_text
    assert response.usage.total_tokens == 17
    assert [item.type for item in response.output].count("web_search_call") == 1


@pytest.mark.asyncio
async def test_gemini_custom_reasoning_budget_is_forwarded():
    seen = {}

    async def handler(request: httpx.Request):
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "status": "incomplete",
            "steps": [],
            "errors": [{"code": "budget_exceeded", "message": "limit"}],
            "usage": {"total_thought_tokens": 2048, "total_tokens": 2048},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(
            http, thinking_level="minimal", total_output_tokens=2048,
        ).create(model="gemini-test", input="hello", max_output_tokens=200, store=False)
    finally:
        await http.aclose()

    config = seen["json"]["generation_config"]
    assert config["thinking_level"] == "minimal"
    assert config["max_output_tokens"] == 2048
    assert response.status == "incomplete"
    assert response.usage.output_tokens is None
    assert response.usage.output_tokens_details.reasoning_tokens == 2048
    assert response._hina_error_codes == ["budget_exceeded"]


@pytest.mark.asyncio
async def test_gemini_http_error_exposes_safe_diagnostics():
    async def handler(request: httpx.Request):
        return httpx.Response(400, json={
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "generation_config.foo is not supported",
            }
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderAPIError) as caught:
            await _GeminiResponses(http).create(
                model="gemini-test", input="secret prompt", max_output_tokens=200, store=False,
            )
    finally:
        await http.aclose()

    error = caught.value
    assert error.provider == "gemini"
    assert error.status_code == 400
    assert error.error_code == "INVALID_ARGUMENT"
    assert error.error_message == "generation_config.foo is not supported"
    assert "secret prompt" not in error.safe_diagnostic


@pytest.mark.asyncio
async def test_openrouter_translates_search_to_web_plugin():
    response = NS(status="completed", output_text="ok", output=[], usage=None)
    create = AsyncMock(return_value=response)
    proxy = _OpenRouterResponses(NS(create=create))

    result = await proxy.create(
        model="anthropic/test",
        input="hello",
        tools=[{"type": "web_search", "search_context_size": "low"}],
        tool_choice="required",
    )

    assert result is response
    kwargs = create.await_args.kwargs
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs
    assert kwargs["extra_body"]["plugins"] == [{"id": "web", "max_results": 3}]
