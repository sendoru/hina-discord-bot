import json
from types import SimpleNamespace as NS

import pytest

from hina_bot.ai.local_tools import (
    LocalToolCall,
    LocalToolError,
    LocalToolExecutor,
    LocalToolRegistry,
    LocalToolSpec,
    ToolRoundLimitError,
    extract_local_tool_calls,
)


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
