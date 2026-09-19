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


def test_gemini_input_translates_function_call_and_result_steps():
    value = _gemini_input([
        {"role": "user", "content": "계산해줘"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "calculator",
            "arguments": '{"expression":"1+2"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "name": "calculator",
            "output": '{"ok":true,"result":3}',
        },
    ])

    assert value[1] == {
        "type": "function_call",
        "id": "call_1",
        "name": "calculator",
        "arguments": {"expression": "1+2"},
    }
    assert value[2] == {
        "type": "function_result",
        "call_id": "call_1",
        "name": "calculator",
        "result": [{"type": "text", "text": '{"ok":true,"result":3}'}],
    }


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
    assert payload["system_instruction"].startswith("system")
    assert "외부 확인이 필수" in payload["system_instruction"]
    assert payload["tools"] == [{"type": "google_search", "search_types": ["web_search"]}]
    assert payload["generation_config"]["tool_choice"] == "auto"
    assert payload["generation_config"]["thinking_level"] == "low"
    assert payload["generation_config"]["max_output_tokens"] == 200
    assert response.status == "completed"
    assert "example.com/source" in response.output_text
    assert response.usage.total_tokens == 17
    assert [item.type for item in response.output].count("web_search_call") == 1


@pytest.mark.asyncio
async def test_gemini_translates_local_function_tool_and_call_output():
    payloads = []

    async def handler(request: httpx.Request):
        payload = __import__("json").loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(200, json={
                "id": "int_1",
                "status": "completed",
                "steps": [{
                    "type": "function_call",
                    "id": "call_1",
                    "name": "echo",
                    "arguments": {"value": "hello"},
                }],
                "usage": {},
            })
        return httpx.Response(200, json={
            "id": "int_2",
            "status": "completed",
            "steps": [{
                "type": "model_output",
                "content": [{"type": "text", "text": "done"}],
            }],
            "usage": {},
        })

    tool = {
        "type": "function",
        "name": "echo",
        "description": "Echo a value.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    }
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first = await _GeminiResponses(http).create(
            model="gemini-test",
            input="hello",
            tools=[tool],
            tool_choice="auto",
        )
        assert first.output[0].type == "function_call"
        assert first.output[0].call_id == "call_1"
        assert first.output[0].name == "echo"
        assert __import__("json").loads(first.output[0].arguments) == {"value": "hello"}

        second = await _GeminiResponses(http).create(
            model="gemini-test",
            input=[
                {"role": "user", "content": "hello"},
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "echo",
                    "arguments": '{"value":"hello"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "name": "echo",
                    "output": '{"ok":true,"result":{"value":"hello"}}',
                },
            ],
            tools=[tool],
            tool_choice="auto",
        )
    finally:
        await http.aclose()

    assert payloads[0]["tools"] == [tool]
    assert payloads[1]["input"][-2]["type"] == "function_call"
    assert payloads[1]["input"][-1]["type"] == "function_result"
    assert second.output_text == "done"


@pytest.mark.asyncio
async def test_gemini_retries_tool_call_overflow_once():
    payloads = []

    async def handler(request: httpx.Request):
        payloads.append(__import__("json").loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(400, json={
                "error": {
                    "code": "Model generated function call(s).",
                    "message": (
                        "Model generated too many tool calls. Please retry the request. "
                        "If the issue persists, include this error message in the retry prompt."
                    ),
                }
            })
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [{
                "type": "model_output",
                "content": [{"type": "text", "text": "재시도 성공"}],
            }],
            "usage": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(http).create(
            model="gemini-test",
            instructions="system",
            input="질문",
            tools=[{"type": "web_search", "search_context_size": "low"}],
            tool_choice="required",
        )
    finally:
        await http.aclose()

    assert len(payloads) == 2
    assert payloads[0]["generation_config"]["tool_choice"] == "auto"
    assert payloads[1]["generation_config"]["tool_choice"] == "auto"
    assert "too many tool calls" in payloads[1]["system_instruction"]
    assert "최대 1회" in payloads[1]["system_instruction"]
    assert response.output_text == "재시도 성공"


@pytest.mark.asyncio
async def test_gemini_common_generation_budget_is_forwarded():
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
            http, thinking_level="minimal",
        ).create(model="gemini-test", input="hello", max_output_tokens=2048, store=False)
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
async def test_gemini_per_request_routing_overrides_thinking_level():
    seen = {}

    async def handler(request: httpx.Request):
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"status": "completed", "steps": [], "usage": {}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await _GeminiResponses(http, thinking_level="low").create(
            model="gemini-smart",
            input="question",
            max_output_tokens=8192,
            thinking_level="medium",
        )
    finally:
        await http.aclose()

    config = seen["json"]["generation_config"]
    assert config["thinking_level"] == "medium"
    assert config["max_output_tokens"] == 8192


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



@pytest.mark.asyncio
async def test_openrouter_preserves_function_tools_when_web_plugin_is_enabled():
    response = NS(status="completed", output_text="ok", output=[], usage=None)
    create = AsyncMock(return_value=response)
    proxy = _OpenRouterResponses(NS(create=create))
    function_tool = {
        "type": "function",
        "name": "echo",
        "description": "Echo.",
        "parameters": {"type": "object", "properties": {}},
    }

    await proxy.create(
        model="anthropic/test",
        input="hello",
        tools=[
            {"type": "web_search", "search_context_size": "low"},
            function_tool,
        ],
        tool_choice="auto",
    )

    kwargs = create.await_args.kwargs
    assert kwargs["tools"] == [function_tool]
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["extra_body"]["plugins"] == [{"id": "web", "max_results": 3}]
