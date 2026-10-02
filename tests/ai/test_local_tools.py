import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.local_tools import (
    CURRENT_LOCAL_TOOLS,
    LocalToolCall,
    LocalToolError,
    LocalToolExecutor,
    LocalToolRegistry,
    LocalToolSpec,
    ToolRoundLimitError,
    extract_local_tool_calls,
)
from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def spec():
    return LocalToolSpec(
        name="echo",
        description="Echo one string.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )


def function_response(call_id="call_1", arguments='{"value":"hello"}'):
    return NS(
        status="completed",
        output_text="",
        output=[
            NS(
                type="function_call",
                id=call_id,
                call_id=call_id,
                name="echo",
                arguments=arguments,
            )
        ],
    )


def final_response(text="done"):
    return NS(status="completed", output_text=text, output=[])


def test_registry_exposes_only_explicit_function_schemas():
    registry = LocalToolRegistry()
    registry.register(spec(), lambda arguments: arguments)

    assert registry.schemas() == [{
        "type": "function",
        "name": "echo",
        "description": "Echo one string.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    }]


def test_extract_local_tool_calls_rejects_non_object_arguments():
    response = function_response(arguments='["not", "an", "object"]')

    calls = extract_local_tool_calls(response)

    assert calls == (
        LocalToolCall("call_1", "echo", None, "invalid_arguments"),
    )


@pytest.mark.asyncio
async def test_registry_executes_sync_and_async_handlers():
    registry = LocalToolRegistry()
    registry.register(spec(), lambda arguments: {"value": arguments["value"]})

    result = await registry.execute(LocalToolCall("1", "echo", {"value": "sync"}))
    assert not result.is_error
    assert result.output == {"value": "sync"}

    async_registry = LocalToolRegistry()

    async def handler(arguments):
        return {"value": arguments["value"]}

    async_registry.register(spec(), handler)
    async_result = await async_registry.execute(
        LocalToolCall("2", "echo", {"value": "async"})
    )
    assert not async_result.is_error
    assert async_result.output == {"value": "async"}


@pytest.mark.asyncio
async def test_registry_fails_closed_without_leaking_handler_exception():
    registry = LocalToolRegistry()

    def handler(_arguments):
        raise RuntimeError("secret database path")

    registry.register(spec(), handler)

    result = await registry.execute(LocalToolCall("1", "echo", {"value": "x"}))

    assert result.is_error
    assert result.output == {"error": "internal_tool_error"}
    assert "secret" not in json.dumps(result.provider_input())


@pytest.mark.asyncio
async def test_expected_tool_error_exposes_only_safe_code():
    registry = LocalToolRegistry()

    def handler(_arguments):
        raise LocalToolError("out_of_range")

    registry.register(spec(), handler)

    result = await registry.execute(LocalToolCall("1", "echo", {"value": "x"}))

    assert result.is_error
    assert result.output == {"error": "out_of_range"}


@pytest.mark.asyncio
async def test_executor_replays_reasoning_item_before_function_result():
    registry = LocalToolRegistry()
    registry.register(spec(), lambda arguments: {"echo": arguments["value"]})
    executor = LocalToolExecutor(registry)
    requests = []
    responses = [
        NS(
            status="completed",
            output_text="",
            output=[
                {
                    "type": "reasoning",
                    "id": "reasoning_1",
                    "summary": [],
                },
                NS(
                    type="function_call",
                    id="call_1",
                    call_id="call_1",
                    name="echo",
                    arguments='{"value":"hello"}',
                ),
            ],
        ),
        final_response(),
    ]

    async def send(request):
        requests.append(request)
        return responses[len(requests) - 1]

    await executor.run(send, {"model": "test", "input": "hello"})

    followup = requests[1]["input"]
    assert followup[-3] == {
        "type": "reasoning",
        "id": "reasoning_1",
        "summary": [],
    }
    assert followup[-2]["type"] == "function_call"
    assert followup[-1]["type"] == "function_call_output"
    assert "name" not in followup[-1]


@pytest.mark.asyncio
async def test_executor_runs_function_and_appends_provider_neutral_result():
    registry = LocalToolRegistry()
    registry.register(spec(), lambda arguments: {"echo": arguments["value"]})
    executor = LocalToolExecutor(registry)
    requests = []
    responses = [function_response(), final_response("finished")]

    async def send(request):
        requests.append(request)
        return responses[len(requests) - 1]

    result = await executor.run(
        send,
        {
            "model": "test",
            "input": [{"role": "user", "content": "hello"}],
            "tools": registry.schemas(),
            "tool_choice": "auto",
        },
    )

    assert result.response.output_text == "finished"
    assert result.local_calls == 1
    assert len(result.responses) == 2
    followup = requests[1]
    assert followup["tool_choice"] == "auto"
    assert followup["input"][-2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "echo",
        "arguments": '{"value":"hello"}',
    }
    tool_output = followup["input"][-1]
    assert tool_output["type"] == "function_call_output"
    assert tool_output["call_id"] == "call_1"
    assert json.loads(tool_output["output"]) == {
        "ok": True,
        "result": {"echo": "hello"},
    }


@pytest.mark.asyncio
async def test_executor_returns_unknown_tool_error_to_model():
    registry = LocalToolRegistry()
    executor = LocalToolExecutor(registry)
    requests = []
    responses = [
        NS(
            status="completed",
            output_text="",
            output=[
                NS(
                    type="function_call",
                    id="call_1",
                    call_id="call_1",
                    name="missing",
                    arguments="{}",
                )
            ],
        ),
        final_response(),
    ]

    async def send(request):
        requests.append(request)
        return responses[len(requests) - 1]

    await executor.run(send, {"model": "test", "input": "hello"})

    output = json.loads(requests[1]["input"][-1]["output"])
    assert output == {
        "ok": False,
        "result": {"error": "unknown_tool"},
    }


@pytest.mark.asyncio
async def test_executor_stops_after_bounded_tool_rounds():
    registry = LocalToolRegistry()
    registry.register(spec(), lambda arguments: arguments)
    executor = LocalToolExecutor(registry, max_rounds=1)

    async def send(_request):
        return function_response()

    with pytest.raises(ToolRoundLimitError):
        await executor.run(send, {"model": "test", "input": "hello"})


@pytest.mark.asyncio
async def test_answer_auto_executes_request_scoped_local_tool():
    registry = LocalToolRegistry()
    roster_spec = LocalToolSpec(
        name="get_current_channel_members",
        description="current channel members",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )
    registry.register(
        roster_spec,
        lambda _arguments: {
            "count": 1,
            "members": [{
                "user_id": "200",
                "server_nickname": "tag : uhe",
                "global_name": "Uhe",
                "username": "2_718281",
            }],
        },
    )

    client = NS(provider_name="openai", close=AsyncMock())
    llm = LLM(
        Settings(
            discord_token="test",
            db_path=":memory:",
            provider="openai",
            model_routing_mode="fixed",
            routing_classifier_mode="off",
            chat_web_search=False,
        ),
        client=client,
    )
    llm.usage.request = AsyncMock(side_effect=[
        NS(
            status="completed",
            output_text="",
            output=[NS(
                type="function_call",
                id="call-1",
                call_id="call-1",
                name="get_current_channel_members",
                arguments="{}",
            )],
        ),
        NS(status="completed", output_text="<@200>", output=[]),
    ])
    store = Store(":memory:")
    token = CURRENT_LOCAL_TOOLS.set(registry)
    try:
        answer = await llm.answer(
            store,
            Scope(1, 10, 100),
            "caller",
            "으혜 불러줘",
            use_memory=False,
        )
        calls = list(llm.usage.request.await_args_list)
    finally:
        CURRENT_LOCAL_TOOLS.reset(token)
        await llm.close()
        store.close()

    assert answer == "<@200>"
    assert len(calls) == 2
    first = calls[0].kwargs
    assert {
        "type": "function",
        "name": "get_current_channel_members",
        "description": "current channel members",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    } in first["tools"]
    assert first["tool_choice"] == "auto"

    second = calls[1].kwargs
    tool_output = second["input"][-1]
    assert tool_output["type"] == "function_call_output"
    payload = json.loads(tool_output["output"])
    assert payload["ok"] is True
    assert payload["result"]["members"][0]["username"] == "2_718281"
