from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.delegated_code_execution import (
    MAX_INPUT_BYTES,
    attach_delegated_code_execution,
    code_execution_backend,
    delegated_model_settings,
)
from hina_bot.ai.local_tools import LocalToolCall, LocalToolRegistry, LocalToolSpec
from hina_bot.ai.vision import VISION_REQUEST_ACTIVE


def result(*, failed=False, executed=True):
    return NS(
        status="completed",
        output_text="42",
        output=[
            NS(type="code_execution_call", id="c1"),
            NS(type="code_execution_result", call_id="c1", result="42", is_error=failed),
        ] if executed else [],
    )


def build(worker, original=None):
    usage = NS(request=worker)
    client = object()
    registry = attach_delegated_code_execution(
        original, client=client, usage=usage, model="gemini-test",
        timeout=7.0, thinking_level="minimal",
    )
    return registry, client


def call(task="compute 6*7", data="", call_id="1"):
    return LocalToolCall(call_id, "request_computation", {"task": task, "input_data": data})


@pytest.mark.parametrize(("provider", "audio", "pdf", "expected"), [
    ("gemini", False, False, "native"),
    ("gemini", True, False, "delegated"),
    ("gemini", False, True, "delegated"),
    ("gemini", True, True, "delegated"),
    ("openai", True, True, "native"),
    ("openrouter", False, False, "none"),
])
def test_backend(provider, audio, pdf, expected):
    assert code_execution_backend(provider, has_audio=audio, has_pdf=pdf) == expected


@pytest.mark.asyncio
async def test_only_minimum_text_and_managed_tool_are_sent():
    async def run(*args, **kwargs):
        assert VISION_REQUEST_ACTIVE.get() is False
        return result()

    worker = AsyncMock(side_effect=run)
    original = LocalToolRegistry()
    original.register(LocalToolSpec("other", "other", {"type": "object"}), lambda _: {})
    registry, client = build(worker, original)
    assert len(original.schemas()) == 1
    assert len(registry.schemas()) == 2
    token = VISION_REQUEST_ACTIVE.set(True)
    try:
        first = await registry.execute(call())
        second = await registry.execute(call(call_id="2"))
        assert VISION_REQUEST_ACTIVE.get() is True
    finally:
        VISION_REQUEST_ACTIVE.reset(token)
    assert first.output == second.output
    assert first.output["executed"] is True
    assert first.output["execution_output"] == "42"
    assert second.call_id == "2"
    worker.assert_awaited_once()
    args, kwargs = worker.await_args
    assert args == (client, "code_execution.delegated")
    assert kwargs["tools"] == [{"type": "code_execution"}]
    assert kwargs["store"] is False
    assert kwargs["retry_empty"] is False
    assert kwargs["deadline_seconds"] == 7.0
    assert kwargs["route_metadata"]["requested_thinking_level"] == "minimal"
    assert kwargs["route_metadata"]["requested_max_output_tokens"] == 4096
    assert isinstance(kwargs["input"], str)
    assert "compute 6*7" in kwargs["input"]
    assert "previous_interaction_id" not in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    result(failed=True),
    result(executed=False),
    NS(status="completed", output_text="42", output=[
        *result().output, NS(type="code_execution_call", id="unfinished"),
    ]),
    NS(status="incomplete", output=[], output_text="42"),
])
async def test_unverified_execution_never_claims_success(response):
    worker = AsyncMock(return_value=response)
    registry, _ = build(worker)
    first = await registry.execute(call())
    duplicate = await registry.execute(call(call_id="2"))
    assert first.is_error and duplicate.is_error
    worker.assert_awaited_once()


@pytest.mark.asyncio
async def test_budget_and_failure():
    worker = AsyncMock(side_effect=TimeoutError)
    registry, _ = build(worker)
    oversized = await registry.execute(call(data="x" * MAX_INPUT_BYTES))
    assert oversized.output["error"] == "computation_input_too_large"
    assert worker.await_count == 0
    assert (await registry.execute(call())).is_error
    assert (await registry.execute(call("compute 1+2"))).is_error
    third = await registry.execute(call("compute 3+4"))
    assert third.output["error"] == "computation_limit_exceeded"
    assert worker.await_count == 2


@pytest.mark.parametrize(("mode", "fixed_level", "fast_level", "expected"), [
    ("adaptive", "high", "minimal", ("gemini-fast", "minimal")),
    ("adaptive", "minimal", "medium", ("gemini-fast", "low")),
    ("adaptive", "high", "low", ("gemini-fast", "low")),
    ("adaptive", "low", "high", ("gemini-fast", "low")),
    ("fixed", "medium", "minimal", ("gemini-fixed", "low")),
    ("fixed", "minimal", "high", ("gemini-fixed", "minimal")),
])
def test_delegated_model_settings(mode, fixed_level, fast_level, expected):
    config = NS(
        model_routing_mode=mode, model="gemini-fixed", fast_model="gemini-fast",
        gemini_thinking_level=fixed_level, gemini_fast_thinking_level=fast_level,
    )
    assert delegated_model_settings(config) == expected


@pytest.mark.parametrize(("provider", "mode", "audio", "pdf", "expected"), [
    ("gemini", "native", False, False, "native"),
    ("gemini", "native", True, False, "none"),
    ("gemini", "native", False, True, "none"),
    ("gemini", "native", True, True, "none"),
    ("gemini", "delegated", False, False, "delegated"),
    ("gemini", "delegated", True, False, "delegated"),
    ("gemini", "delegated", False, True, "delegated"),
    ("openai", "delegated", True, True, "native"),
    ("openrouter", "delegated", False, False, "none"),
])
def test_explicit_backend(provider, mode, audio, pdf, expected):
    assert code_execution_backend(
        provider, mode=mode, has_audio=audio, has_pdf=pdf,
    ) == expected


def test_unimplemented_backend_is_rejected():
    with pytest.raises(ValueError):
        code_execution_backend("gemini", mode="local")
