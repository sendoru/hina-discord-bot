import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.calculator_tool import CALCULATOR_POLICY
from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def function_call_response(expression: str):
    return NS(
        status="completed",
        output_text="",
        output=[
            NS(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name="calculator",
                arguments=json.dumps({"expression": expression}),
            )
        ],
        usage=None,
    )


def final_response(text: str):
    return NS(
        status="completed",
        output_text=text,
        output=[NS(type="message")],
        usage=None,
    )


def client(*responses):
    create = AsyncMock(side_effect=list(responses))
    return NS(
        provider_name="gemini",
        responses=NS(create=create),
        close=AsyncMock(),
    )


def settings(tmp_path):
    return Settings(
        "test",
        "test",
        provider="gemini",
        model="gemini-test",
        fast_model="gemini-test",
        smart_model="gemini-test",
        gemini_thinking_level="minimal",
        gemini_fast_thinking_level="minimal",
        gemini_smart_thinking_level="minimal",
        db_path=str(tmp_path / "admin.sqlite3"),
        usage_log_path="",
        external_context_policy="full",
    )


@pytest.mark.asyncio
async def test_chat_calculator_round_trip_is_available_on_normal_request(tmp_path):
    fake = client(
        function_call_response("3.9 > 3.11"),
        final_response("3.9가 더 커."),
    )
    llm = InformationPipeline(settings(tmp_path), client=fake)
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    try:
        answer = await llm.answer(
            store,
            scope,
            "사용자",
            "3.9랑 3.11 중 뭐가 더 커?",
            channel_context=[],
        )

        assert answer == "3.9가 더 커."
        assert fake.responses.create.await_count == 2

        first = fake.responses.create.await_args_list[0].kwargs
        calculator = next(
            tool for tool in first["tools"]
            if tool.get("type") == "function" and tool.get("name") == "calculator"
        )
        assert calculator["parameters"]["properties"]["expression"]["type"] == "string"
        assert first["tool_choice"] == "auto"
        assert CALCULATOR_POLICY in first["instructions"]

        second = fake.responses.create.await_args_list[1].kwargs
        tool_output = next(
            item for item in second["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        )
        payload = json.loads(tool_output["output"])
        assert payload == {
            "ok": True,
            "result": {
                "expression": "3.9 > 3.11",
                "type": "boolean",
                "value": True,
            },
        }
        assert second["tool_choice"] == "auto"
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_calculator_is_exposed_but_not_forced_on_non_numeric_chat(tmp_path):
    fake = client(final_response("응, 무슨 일이야?"))
    llm = InformationPipeline(settings(tmp_path), client=fake)
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    try:
        answer = await llm.answer(
            store,
            scope,
            "사용자",
            "히나야 안녕",
            channel_context=[],
        )

        assert answer == "응, 무슨 일이야?"
        assert fake.responses.create.await_count == 1
        request = fake.responses.create.await_args.kwargs
        assert any(
            tool.get("name") == "calculator"
            for tool in request["tools"]
            if tool.get("type") == "function"
        )
        assert request["tool_choice"] == "auto"
    finally:
        await llm.close()
        store.close()
