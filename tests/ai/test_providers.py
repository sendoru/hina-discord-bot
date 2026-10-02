from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest

from hina_bot.ai.providers import (
    ProviderAPIError,
    _gemini_input,
    _gemini_output,
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
async def test_gemini_routes_search_through_generate_content_and_normalizes_response():
    seen = {}

    async def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "candidates": [{
                "finishReason": "STOP",
                "content": {
                    "role": "model",
                    "parts": [{"text": "검색 결과야."}],
                },
                "groundingMetadata": {
                    "webSearchQueries": ["test"],
                    "groundingChunks": [{
                        "web": {
                            "uri": "https://example.com/source",
                            "title": "Example",
                        }
                    }],
                },
            }],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "thoughtsTokenCount": 2,
                "cachedContentTokenCount": 1,
                "totalTokenCount": 17,
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
    assert seen["url"].endswith("/v1beta/models/gemini-test:generateContent")
    instruction = payload["system_instruction"]["parts"][0]["text"]
    assert instruction.startswith("system")
    assert "외부 확인이 필수" in instruction
    assert payload["contents"] == [{
        "role": "user",
        "parts": [{"text": "질문"}],
    }]
    assert payload["tools"] == [{"google_search": {}}]
    assert payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "low"
    assert payload["generationConfig"]["maxOutputTokens"] == 200
    assert response.status == "completed"
    assert "example.com/source" in response.output_text
    assert response.usage.total_tokens == 17
    assert [item.type for item in response.output].count("web_search_call") == 1


def test_gemini_output_uses_only_final_model_output_run():
    response = _gemini_output({
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [{
                    "type": "text",
                    "text": "체인 확인 및 응답 길이 준수 (1~4문장), 무대 지시 없이 담백한 반말.",
                }],
            },
            {
                "type": "code_execution_call",
                "id": "code_1",
                "arguments": {"code": "print('solve')", "language": "python"},
            },
            {
                "type": "code_execution_result",
                "call_id": "code_1",
                "result": "solve\n",
                "is_error": False,
            },
            {
                "type": "model_output",
                "content": [{"type": "text", "text": "사과게임 풀이야. "}],
            },
            {
                "type": "model_output",
                "content": [{"type": "text", "text": "이 부분만 보여야 해."}],
            },
        ],
        "usage": {},
    })

    assert response.output_text == "사과게임 풀이야. 이 부분만 보여야 해."
    assert [item.type for item in response.output] == [
        "message",
        "code_execution_call",
        "code_execution_result",
        "message",
        "message",
    ]
    assert response.output[0].content[0].text.startswith("체인 확인 및 응답 길이 준수")


@pytest.mark.asyncio
async def test_gemini_managed_code_execution_stays_inside_one_interaction():
    seen = {}

    async def handler(request: httpx.Request):
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [
                {
                    "type": "code_execution_call",
                    "id": "code_1",
                    "arguments": {
                        "code": "print(3.9 > 3.11)",
                        "language": "python",
                    },
                },
                {
                    "type": "code_execution_result",
                    "call_id": "code_1",
                    "result": "True\n",
                    "is_error": False,
                },
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": "3.9가 더 커."}],
                },
            ],
            "usage": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(http).create(
            model="gemini-test",
            input="3.9랑 3.11 중 뭐가 더 커?",
            tools=[{"type": "code_execution"}],
            tool_choice="auto",
        )
    finally:
        await http.aclose()

    assert seen["json"]["tools"] == [{"type": "code_execution"}]
    assert response.output_text == "3.9가 더 커."
    assert [item.type for item in response.output] == [
        "code_execution_call",
        "code_execution_result",
        "message",
    ]
    assert response.output[1].result == "True\n"


@pytest.mark.asyncio
async def test_gemini_generate_content_preserves_search_and_code_execution_together():
    seen = {}

    async def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "candidates": [{
                "finishReason": "STOP",
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "계산해볼게."},
                        {
                            "executableCode": {
                                "id": "code_1",
                                "language": "PYTHON",
                                "code": "print(42)",
                            }
                        },
                        {
                            "codeExecutionResult": {
                                "id": "code_1",
                                "outcome": "OUTCOME_OK",
                                "output": "42\\n",
                            }
                        },
                        {"text": "답은 42야."},
                    ],
                },
                "groundingMetadata": {
                    "webSearchQueries": ["answer 42"],
                },
            }],
            "usageMetadata": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(http).create(
            model="gemini-test",
            input="질문",
            tools=[
                {"type": "web_search", "search_context_size": "low"},
                {"type": "code_execution"},
            ],
            tool_choice="auto",
        )
    finally:
        await http.aclose()

    assert seen["url"].endswith("/v1beta/models/gemini-test:generateContent")
    assert seen["json"]["tools"] == [
        {"google_search": {}},
        {"code_execution": {}},
    ]
    assert response.output_text == "답은 42야."
    assert [item.type for item in response.output] == [
        "web_search_call",
        "message",
        "code_execution_call",
        "code_execution_result",
        "message",
    ]


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
async def test_gemini_non_search_requests_stay_on_interactions():
    seen = {}

    async def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [{
                "type": "model_output",
                "content": [{"type": "text", "text": "응."}],
            }],
            "usage": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(http).create(
            model="gemini-test",
            input="안녕",
            tools=[{"type": "code_execution"}],
            tool_choice="auto",
        )
    finally:
        await http.aclose()

    assert seen["url"].endswith("/v1beta/interactions")
    assert seen["json"]["tools"] == [{"type": "code_execution"}]
    assert "tool_choice" not in seen["json"]["generation_config"]
    assert response.output_text == "응."


@pytest.mark.asyncio
async def test_gemini_store_setting_overrides_per_request_false_for_all_endpoints():
    seen = []

    async def handler(request: httpx.Request):
        seen.append((str(request.url), __import__("json").loads(request.content)))
        if str(request.url).endswith("/v1beta/interactions"):
            return httpx.Response(200, json={
                "status": "completed",
                "steps": [],
                "usage": {},
            })
        return httpx.Response(200, json={
            "candidates": [{
                "finishReason": "STOP",
                "content": {"role": "model", "parts": [{"text": "ok"}]},
            }],
            "usageMetadata": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        responses = _GeminiResponses(http, store_interactions=True)
        await responses.create(
            model="gemini-test",
            input="plain",
            store=False,
        )
        await responses.create(
            model="gemini-test",
            input="search",
            store=False,
            tools=[{"type": "web_search"}],
        )
    finally:
        await http.aclose()

    assert [payload["store"] for _, payload in seen] == [True, True]
    assert seen[0][0].endswith("/v1beta/interactions")
    assert seen[1][0].endswith("/v1beta/models/gemini-test:generateContent")


@pytest.mark.asyncio
async def test_gemini_store_setting_callable_is_read_for_each_request():
    seen = []
    state = {"enabled": False}

    async def handler(request: httpx.Request):
        seen.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [],
            "usage": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        responses = _GeminiResponses(
            http,
            store_interactions=lambda: state["enabled"],
        )
        await responses.create(model="gemini-test", input="first", store=False)
        state["enabled"] = True
        await responses.create(model="gemini-test", input="second", store=False)
    finally:
        await http.aclose()

    assert [payload["store"] for payload in seen] == [False, True]


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
