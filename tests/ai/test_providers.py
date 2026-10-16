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


def test_gemini_input_preserves_native_thought_and_function_call_steps():
    thought = {"type": "thought", "signature": "sig", "summary": []}
    function_call = {
        "type": "function_call",
        "id": "call_1",
        "name": "echo",
        "arguments": {"value": "hello"},
    }

    value = _gemini_input([
        {"role": "user", "content": "hello"},
        thought,
        function_call,
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "name": "echo",
            "output": '{"ok":true}',
        },
    ])

    assert value[1] == thought
    assert value[2] == function_call
    assert value[3]["name"] == "echo"


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
async def test_gemini_interaction_restores_function_result_name_from_call_id():
    seen = {}

    async def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [{
                "type": "model_output",
                "content": [{"type": "text", "text": "done"}],
            }],
            "usage": {},
        })

    tool = {
        "type": "function",
        "name": "get_current_channel_members",
        "description": "current channel members",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(http).create(
            model="gemini-test",
            input=[
                {"role": "user", "content": "나기나기 핑해줘"},
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "get_current_channel_members",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": '{"ok":true,"result":{"members":[]}}',
                },
            ],
            tools=[tool],
            tool_choice="auto",
        )
    finally:
        await http.aclose()

    assert seen["url"].endswith("/v1beta/interactions")
    assert seen["json"]["input"][-2] == {
        "type": "function_call",
        "id": "call_1",
        "name": "get_current_channel_members",
        "arguments": {},
    }
    assert seen["json"]["input"][-1]["type"] == "function_result"
    assert seen["json"]["input"][-1]["call_id"] == "call_1"
    assert seen["json"]["input"][-1]["name"] == "get_current_channel_members"
    assert response.output_text == "done"



@pytest.mark.asyncio
async def test_gemini_search_uses_interactions_and_normalizes_grounded_response():
    seen = {}

    async def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "id": "int_search_1",
            "status": "completed",
            "steps": [
                {
                    "type": "google_search_call",
                    "id": "gs_1",
                    "arguments": {"queries": ["test"]},
                },
                {
                    "type": "google_search_result",
                    "call_id": "gs_1",
                    "result": [{"search_suggestions": []}],
                },
                {
                    "type": "model_output",
                    "content": [{
                        "type": "text",
                        "text": "검색 결과야.",
                        "annotations": [{
                            "type": "url_citation",
                            "url": "https://example.com/source",
                            "title": "Example",
                        }],
                    }],
                },
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
    assert seen["url"].endswith("/v1beta/interactions")
    assert payload["system_instruction"].startswith("system")
    assert "외부 확인이 필수" in payload["system_instruction"]
    assert payload["input"] == [{
        "type": "user_input",
        "content": [{"type": "text", "text": "질문"}],
    }]
    assert payload["tools"] == [{"type": "google_search"}]
    assert payload["generation_config"]["thinking_level"] == "low"
    assert payload["generation_config"]["max_output_tokens"] == 200
    assert "tool_choice" not in payload["generation_config"]
    assert response.status == "completed"
    assert "example.com/source" in response.output_text
    assert response.usage.total_tokens == 17
    assert response._hina_web_search_calls == 1
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
async def test_gemini_url_context_combines_with_search_and_function_tools():
    seen = {}
    steps = [
        {"type": "url_context_call", "id": "url_1", "arguments": {
            "urls": ["https://example.com/docs"],
        }, "signature": "signed_call"},
        {"type": "url_context_result", "call_id": "url_1", "result": [
            {"url": "https://example.com/docs", "status": "success"},
            {"url": "https://example.com/missing", "status": "paywall"},
        ], "signature": "signed_result"},
        {"type": "google_search_call", "id": "gs_1"},
        {"type": "google_search_result", "call_id": "gs_1", "result": []},
        {"type": "model_output", "content": [{
            "type": "text",
            "text": "문서 요약이야.",
            "annotations": [{
                "type": "url_citation",
                "url": "https://example.com/docs",
                "title": "Docs",
            }],
        }]},
    ]

    async def handler(request: httpx.Request):
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "status": "completed",
            "steps": steps,
            "usage": {"total_input_tokens": 20, "total_tokens": 25},
        })

    tool = {
        "type": "function", "name": "lookup", "description": "Look up a member",
        "parameters": {"type": "object", "properties": {}},
    }
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(http).create(
            model="gemini-test",
            input="이 링크 내용을 요약해 줘 https://example.com/docs",
            tools=[{"type": "url_context"}, {"type": "web_search"}, tool,
                   {"type": "code_execution"}],
            tool_choice="auto",
        )
    finally:
        await http.aclose()

    assert seen["json"]["tools"] == [
        {"type": "google_search"}, {"type": "url_context"}, tool,
        {"type": "code_execution"},
    ]
    assert "tool_choice" not in seen["json"]["generation_config"]
    assert response.status == "completed"
    assert response._hina_url_context_calls == 1
    assert response._hina_url_context_statuses == ("success", "paywall")
    assert response._hina_web_search_calls == 1
    assert [item.type for item in response.output][:2] == [
        "url_context_call", "web_search_call",
    ]
    assert "example.com/docs" in response.output_text
    assert _gemini_input(list(response._hina_interaction_steps)) == steps


@pytest.mark.asyncio
async def test_gemini_url_context_auto_fallback_preserves_tool_in_generate_content():
    calls = []

    async def handler(request: httpx.Request):
        payload = __import__("json").loads(request.content)
        calls.append((str(request.url), payload))
        if str(request.url).endswith("/v1beta/interactions"):
            return httpx.Response(400, json={
                "error": {"status": "INVALID_ARGUMENT",
                          "message": "url_context tool is unavailable"},
            })
        return httpx.Response(200, json={
            "candidates": [{
                "content": {"parts": [{"text": "문서 요약"}]},
                "finishReason": "STOP",
                "urlContextMetadata": {"urlMetadata": [
                    {"retrievedUrl": "https://example.com/docs",
                     "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"},
                ]},
            }],
            "usageMetadata": {"promptTokenCount": 10},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(
            http, settings=NS(gemini_search_api_mode="auto"),
        ).create(model="gemini-test", input="https://example.com/docs",
                 tools=[{"type": "url_context"}])
    finally:
        await http.aclose()

    assert len(calls) == 2
    assert calls[0][1]["tools"] == [{"type": "url_context"}]
    assert calls[1][1]["tools"] == [{"url_context": {}}]
    assert response._hina_gemini_api == "generate_content"
    assert response._hina_url_context_calls == 1
    assert response._hina_url_context_statuses == ("success",)


def test_gemini_url_context_requires_action_is_diagnostic_not_success():
    response = _gemini_output({
        "status": "requires_action",
        "steps": [
            {"type": "url_context_call", "id": "url_1"},
            {"type": "url_context_result", "call_id": "url_1", "result": [
                {"url": "https://example.com", "status": "error"},
            ]},
        ],
        "usage": {},
    })
    assert response._hina_error_codes == ["unexpected_requires_action"]
    assert response._hina_url_context_calls == 1
    assert response._hina_url_context_statuses == ("error",)


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
async def test_gemini_interactions_preserves_search_and_code_execution_together():
    seen = {}

    async def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [
                {"type": "google_search_call", "id": "gs_1"},
                {"type": "google_search_result", "call_id": "gs_1", "result": []},
                {"type": "model_output", "content": [{"type": "text", "text": "계산해볼게."}]},
                {
                    "type": "code_execution_call",
                    "id": "code_1",
                    "arguments": {"code": "print(42)", "language": "python"},
                },
                {
                    "type": "code_execution_result",
                    "call_id": "code_1",
                    "result": "42\\n",
                    "is_error": False,
                },
                {"type": "model_output", "content": [{"type": "text", "text": "답은 42야."}]},
            ],
            "usage": {},
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

    assert seen["url"].endswith("/v1beta/interactions")
    assert seen["json"]["tools"] == [
        {"type": "google_search"},
        {"type": "code_execution"},
    ]
    assert "tool_choice" not in seen["json"]["generation_config"]
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
async def test_gemini_interactions_preserves_function_schema_with_search():
    seen = {}

    async def handler(request: httpx.Request):
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"status": "completed", "steps": [], "usage": {}})

    tool = {
        "type": "function",
        "name": "lookup",
        "description": "Lookup.",
        "parameters": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    }

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await _GeminiResponses(http).create(
            model="gemini-test",
            input="search",
            tools=[{"type": "web_search"}, tool],
            tool_choice="auto",
        )
    finally:
        await http.aclose()

    assert seen["json"]["tools"] == [{"type": "google_search"}, tool]
    assert "tool_choice" not in seen["json"]["generation_config"]


@pytest.mark.asyncio
async def test_gemini_interactions_restores_function_response_name_with_search():
    seen = {}

    async def handler(request: httpx.Request):
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "done"}]}],
            "usage": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await _GeminiResponses(http).create(
            model="gemini-test",
            input=[
                {"role": "user", "content": "으혜 불러줘"},
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "get_current_channel_members",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": '{"ok":true,"result":{"members":[]}}',
                },
            ],
            tools=[
                {"type": "web_search"},
                {
                    "type": "function",
                    "name": "get_current_channel_members",
                    "description": "current channel members",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            ],
            tool_choice="auto",
        )
    finally:
        await http.aclose()

    function_response = seen["json"]["input"][-1]
    assert function_response["type"] == "function_result"
    assert function_response["call_id"] == "call_1"
    assert function_response["name"] == "get_current_channel_members"
    assert response.output_text == "done"

@pytest.mark.asyncio
async def test_gemini_stateful_function_result_uses_previous_interaction_id():
    payloads = []

    async def handler(request: httpx.Request):
        payload = __import__("json").loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(200, json={
                "id": "int_1",
                "status": "requires_action",
                "steps": [{
                    "type": "function_call",
                    "id": "call_1",
                    "name": "get_current_channel_members",
                    "arguments": {},
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
        "name": "get_current_channel_members",
        "description": "current channel members",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        responses = _GeminiResponses(http)
        first = await responses.create(
            model="gemini-test",
            input="나기나기 핑해줘",
            tools=[tool],
            tool_choice="auto",
            store=True,
        )
        assert first._hina_interaction_id == "int_1"

        second = await responses.create(
            model="gemini-test",
            previous_interaction_id=first._hina_interaction_id,
            input=[{
                "type": "function_call_output",
                "call_id": "call_1",
                "name": "get_current_channel_members",
                "output": '{"ok":true,"result":{"members":[]}}',
            }],
            tools=[tool],
            tool_choice="auto",
            store=True,
        )
    finally:
        await http.aclose()

    assert payloads[1]["previous_interaction_id"] == "int_1"
    assert payloads[1]["input"] == [{
        "type": "function_result",
        "call_id": "call_1",
        "name": "get_current_channel_members",
        "result": [{
            "type": "text",
            "text": '{"ok":true,"result":{"members":[]}}',
        }],
    }]
    assert second.output_text == "done"


def test_gemini_built_in_requires_action_without_function_call_is_diagnostic():
    response = _gemini_output({
        "status": "requires_action",
        "steps": [
            {"type": "google_search_call", "id": "gs_1"},
            {"type": "google_search_result", "call_id": "gs_1", "result": []},
        ],
        "usage": {},
    })
    assert response.status == "requires_action"
    assert response._hina_error_codes == ["unexpected_requires_action"]


@pytest.mark.asyncio
async def test_gemini_mixed_search_then_local_function_replays_native_steps():
    from hina_bot.ai.local_tools import LocalToolExecutor, LocalToolRegistry, LocalToolSpec

    payloads = []

    async def handler(request: httpx.Request):
        payload = __import__("json").loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(200, json={
                "status": "requires_action",
                "steps": [
                    {"type": "google_search_call", "id": "gs_1",
                     "signature": "search_sig", "arguments": {"queries": ["query"]}},
                    {"type": "google_search_result", "call_id": "gs_1",
                     "signature": "result_sig", "result": []},
                    {"type": "thought", "signature": "thought_sig", "summary": []},
                    {"type": "function_call", "id": "call_1", "name": "echo",
                     "signature": "function_sig", "arguments": {"value": "hello"}},
                ],
                "usage": {},
            })
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [{
                "type": "model_output",
                "content": [{"type": "text", "text": "finished"}],
            }],
            "usage": {},
        })

    registry = LocalToolRegistry()
    registry.register(LocalToolSpec(
        name="echo",
        description="Echo.",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}},
    ), lambda args: args)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        responses = _GeminiResponses(http)
        request = {
            "model": "gemini-test",
            "input": "hello",
            "tools": [{"type": "web_search"}, *registry.schemas()],
            "tool_choice": "auto",
            "store": False,
        }
        async def send(payload):
            return await responses.create(**payload)

        result = await LocalToolExecutor(registry).run(send, request)
    finally:
        await http.aclose()

    assert result.local_calls == 1
    assert result.response.output_text == "finished"
    assert len(payloads) == 2
    for payload in payloads:
        assert payload["tools"][0] == {"type": "google_search"}
        assert payload["tools"][1]["type"] == "function"
        assert "tool_choice" not in payload["generation_config"]
    steps = payloads[1]["input"]
    assert steps[0]["type"] == "user_input"
    assert [(step["type"], step.get("signature")) for step in steps[1:5]] == [
        ("google_search_call", "search_sig"),
        ("google_search_result", "result_sig"),
        ("thought", "thought_sig"),
        ("function_call", "function_sig"),
    ]
    assert steps[-1]["type"] == "function_result"
    assert steps[-1]["call_id"] == "call_1"
    assert steps[-1]["name"] == "echo"

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unexpected_requires_action", "tool_http_error"])
async def test_gemini_auto_falls_back_for_known_interactions_tool_failures(failure):
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        if calls[-1].endswith("/v1beta/interactions"):
            if failure == "tool_http_error":
                return httpx.Response(400, json={
                    "error": {"status": "INVALID_ARGUMENT",
                              "message": "google_search tool is unsupported"},
                })
            return httpx.Response(200, json={
                "status": "requires_action",
                "steps": [{"type": "google_search_call", "id": "gs_1"}],
                "usage": {},
            })
        return httpx.Response(200, json={
            "candidates": [{
                "content": {"parts": [{"text": "fallback answer"}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = _GeminiResponses(http, settings=NS(gemini_search_api_mode="auto"))
        response = await client.create(
            model="test", input="question", tools=[{"type": "web_search"}]
        )
    finally:
        await http.aclose()

    assert calls == [
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent",
    ]
    assert response.output_text == "fallback answer"
    assert response._hina_gemini_api == "generate_content"


@pytest.mark.asyncio
async def test_gemini_auto_does_not_retry_unrelated_http_errors():
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(400, json={
            "error": {"status": "INVALID_ARGUMENT", "message": "invalid model name"},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        responses = _GeminiResponses(http, settings=NS(gemini_search_api_mode="auto"))
        with pytest.raises(ProviderAPIError):
            await responses.create(
                model="test", input="question", tools=[{"type": "web_search"}]
            )
    finally:
        await http.aclose()
    assert len(calls) == 1
    assert calls[0].endswith("/v1beta/interactions")


@pytest.mark.asyncio
async def test_gemini_search_manual_modes_apply_on_next_turn():
    selected = NS(gemini_search_api_mode="interactions")
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        if calls[-1].endswith("/v1beta/interactions"):
            return httpx.Response(200, json={
                "status": "completed",
                "steps": [{"type": "model_output",
                           "content": [{"type": "text", "text": "interaction"}]}],
                "usage": {},
            })
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "generateContent"}]}}],
            "usageMetadata": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = _GeminiResponses(http, settings=selected)
        first = await client.create(model="test", input="one", tools=[{"type": "web_search"}])
        selected.gemini_search_api_mode = "generate_content"
        second = await client.create(model="test", input="two", tools=[{"type": "web_search"}])
        third = await client.create(model="test", input="plain")
    finally:
        await http.aclose()

    assert [first.output_text, second.output_text, third.output_text] == [
        "interaction", "generateContent", "interaction",
    ]
    assert calls[0].endswith("/v1beta/interactions")
    assert calls[1].endswith("/v1beta/models/test:generateContent")
    assert calls[2].endswith("/v1beta/interactions")


@pytest.mark.asyncio
async def test_gemini_auto_circuit_opens_and_recovers_after_cooldown(monkeypatch):
    from hina_bot.ai import providers

    clock = [100.0]
    monkeypatch.setattr(providers, "monotonic", lambda: clock[0])
    calls = []
    fail_interactions = [True]

    async def handler(request):
        calls.append(str(request.url))
        if calls[-1].endswith("/v1beta/interactions"):
            if fail_interactions[0]:
                return httpx.Response(200, json={
                    "status": "requires_action",
                    "steps": [{"type": "google_search_call", "id": "gs"}],
                    "usage": {},
                })
            return httpx.Response(200, json={
                "status": "completed",
                "steps": [{"type": "model_output",
                           "content": [{"type": "text", "text": "recovered"}]}],
                "usage": {},
            })
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "fallback"}]}}],
            "usageMetadata": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = _GeminiResponses(http, settings=NS(gemini_search_api_mode="auto"))
        for _ in range(2):
            assert (await client.create(
                model="test", input="search", tools=[{"type": "web_search"}]
            )).output_text == "fallback"
        assert client._search_circuit_until == 400.0
        calls_before = len(calls)
        await client.create(model="test", input="search", tools=[{"type": "web_search"}])
        assert len(calls) == calls_before + 1
        assert calls[-1].endswith("/v1beta/models/test:generateContent")
        clock[0] = 401.0
        fail_interactions[0] = False
        answer = await client.create(
            model="test", input="search", tools=[{"type": "web_search"}]
        )
    finally:
        await http.aclose()

    assert answer.output_text == "recovered"
    assert calls[-1].endswith("/v1beta/interactions")
    assert client._search_failures == 0
    assert client._search_circuit_until == 0


@pytest.mark.asyncio
async def test_gemini_auto_does_not_fallback_after_client_function_call():
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={
            "status": "requires_action",
            "steps": [{"type": "google_search_call", "id": "gs_1"}],
            "usage": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = _GeminiResponses(http, settings=NS(gemini_search_api_mode="auto"))
        response = await client.create(
            model="test",
            input=[
                {"type": "function_call_output", "call_id": "call_1",
                 "name": "echo", "output": '{"ok":true}'},
            ],
            tools=[{"type": "web_search"}],
        )
    finally:
        await http.aclose()

    assert response.status == "requires_action"
    assert response._hina_error_codes == ["unexpected_requires_action"]
    assert len(calls) == 1
    assert calls[0].endswith("/v1beta/interactions")


@pytest.mark.asyncio
async def test_gemini_manually_forced_interactions_does_not_auto_fallback():
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={
            "status": "requires_action",
            "steps": [{"type": "google_search_call", "id": "gs_1"}],
            "usage": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = _GeminiResponses(http, settings=NS(gemini_search_api_mode="interactions"))
        response = await client.create(
            model="test", input="search", tools=[{"type": "web_search"}]
        )
    finally:
        await http.aclose()

    assert response.status == "requires_action"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_gemini_generate_content_continuation_is_pinned_across_runtime_changes():
    from hina_bot.ai.local_tools import LocalToolExecutor, LocalToolRegistry, LocalToolSpec

    selected = NS(gemini_search_api_mode="generate_content")
    calls = []
    registry = LocalToolRegistry()

    def execute(args):
        selected.gemini_search_api_mode = "interactions"
        return args

    registry.register(LocalToolSpec(
        "echo", "Echo", {"type": "object", "properties": {"value": {"type": "string"}}}
    ), execute)

    async def handler(request):
        calls.append((str(request.url), __import__("json").loads(request.content)))
        if len(calls) == 1:
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{
                    "functionCall": {"id": "call_1", "name": "echo",
                                     "args": {"value": "hello"}},
                }]}}],
                "usageMetadata": {},
            })
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "finished"}]}}],
            "usageMetadata": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = _GeminiResponses(http, settings=selected)

        async def send(payload):
            return await client.create(**payload)

        result = await LocalToolExecutor(registry).run(send, {
            "model": "test",
            "input": "hello",
            "tools": [{"type": "web_search"}, *registry.schemas()],
        })
    finally:
        await http.aclose()

    assert result.local_calls == 1
    assert result.response.output_text == "finished"
    assert all(url.endswith("/v1beta/models/test:generateContent") for url, _ in calls)
    assert calls[1][1]["contents"][-1]["parts"][0]["functionResponse"]["name"] == "echo"

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
async def test_gemini_respects_per_request_store_with_and_without_search():
    seen = []

    async def handler(request: httpx.Request):
        seen.append((str(request.url), __import__("json").loads(request.content)))
        return httpx.Response(200, json={
            "status": "completed",
            "steps": [],
            "usage": {},
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        responses = _GeminiResponses(http)
        await responses.create(model="gemini-test", input="plain", store=False)
        await responses.create(
            model="gemini-test",
            input="search",
            store=True,
            tools=[{"type": "web_search"}],
        )
    finally:
        await http.aclose()

    assert [payload["store"] for _, payload in seen] == [False, True]
    assert all(url.endswith("/v1beta/interactions") for url, _ in seen)

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
