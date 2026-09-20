from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.managed_tools import CODE_EXECUTION_POLICY, managed_tool_config
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def settings(tmp_path, *, provider="gemini"):
    model = "gemini-test" if provider == "gemini" else "gpt-4.1-mini"
    return Settings(
        "test",
        "test",
        provider=provider,
        model=model,
        fast_model=model,
        smart_model=model,
        gemini_thinking_level="minimal",
        gemini_fast_thinking_level="minimal",
        gemini_smart_thinking_level="minimal",
        db_path=str(tmp_path / "admin.sqlite3"),
        usage_log_path="",
        external_context_policy="full",
    )


def client(text: str, *, provider="gemini"):
    response = NS(
        status="completed",
        output_text=text,
        output=[NS(type="message")],
        usage=None,
    )
    return NS(
        provider_name=provider,
        responses=NS(create=AsyncMock(return_value=response)),
        close=AsyncMock(),
    )


def test_managed_code_execution_maps_to_provider_native_tools():
    assert managed_tool_config("gemini") == [{"type": "code_execution"}]
    assert managed_tool_config("openai") == [{
        "type": "code_interpreter",
        "container": {"type": "auto"},
    }]
    assert managed_tool_config("openrouter") == []


@pytest.mark.asyncio
async def test_normal_gemini_chat_exposes_managed_code_execution_in_one_model_call(tmp_path):
    fake = client("3.9가 더 커.")
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
        assert fake.responses.create.await_count == 1
        request = fake.responses.create.await_args.kwargs
        assert {"type": "code_execution"} in request["tools"]
        assert request["tool_choice"] == "auto"
        assert CODE_EXECUTION_POLICY in request["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_non_numeric_gemini_chat_still_only_makes_one_model_call(tmp_path):
    fake = client("응, 무슨 일이야?")
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
        assert {"type": "code_execution"} in request["tools"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_normal_openai_chat_exposes_code_interpreter_in_one_model_call(tmp_path):
    fake = client("3.9가 더 커.", provider="openai")
    llm = InformationPipeline(settings(tmp_path, provider="openai"), client=fake)
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
        assert fake.responses.create.await_count == 1
        request = fake.responses.create.await_args.kwargs
        assert {
            "type": "code_interpreter",
            "container": {"type": "auto"},
        } in request["tools"]
        assert request["tool_choice"] == "auto"
        assert CODE_EXECUTION_POLICY in request["instructions"]
    finally:
        await llm.close()
        store.close()
