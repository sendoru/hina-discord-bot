from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.audio import CURRENT_AUDIO_INPUTS
from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.managed_tools import (
    CODE_EXECUTION_POLICY,
    ManagedToolCapability,
    managed_tool_config,
)
from hina_bot.ai.model_routing import fixed_model_plan
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def settings(tmp_path, *, provider="gemini"):
    model = "gemini-test" if provider == "gemini" else "gpt-4.1-mini"
    return Settings(
        discord_token="test",
        provider=provider,
        openai_api_key="test" if provider == "openai" else "",
        gemini_api_key="test" if provider == "gemini" else "",
        openrouter_api_key="test" if provider == "openrouter" else "",
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
    assert managed_tool_config("gemini", capabilities=(
        ManagedToolCapability.URL_CONTEXT,
    )) == [{"type": "url_context"}]
    assert managed_tool_config("openai") == [{
        "type": "code_interpreter",
        "container": {"type": "auto"},
    }]
    assert managed_tool_config("openrouter") == []


@pytest.mark.asyncio
async def test_normal_gemini_chat_exposes_managed_code_execution_in_one_model_call(tmp_path):
    fake = client("3.9가 더 커.")
    llm = InformationPipeline(settings(tmp_path), client=fake)
    store = Store.create(":memory:")
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
    store = Store.create(":memory:")
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
async def test_gemini_link_message_exposes_url_context_without_forcing_fetch(tmp_path):
    fake = client("그 문서에 대해 이야기해보자.")
    llm = InformationPipeline(settings(tmp_path), client=fake)
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        await llm.answer(
            store, scope, "사용자",
            "히나야 https://example.com/docs 이거 어떤 문서야?",
            channel_context=[],
        )
        request = fake.responses.create.await_args.kwargs
        assert {"type": "url_context"} in request["tools"]
        assert "tool_choice" not in request or request["tool_choice"] == "auto"
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_normal_openai_chat_exposes_code_interpreter_in_one_model_call(tmp_path):
    fake = client("3.9가 더 커.", provider="openai")
    llm = InformationPipeline(settings(tmp_path, provider="openai"), client=fake)
    store = Store.create(":memory:")
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


@pytest.mark.asyncio
async def test_gemini_audio_delegates_code_execution_and_keeps_other_tools(tmp_path):
    """Audio + code_execution is a Gemini API 400 even when code execution isn't used."""
    fake = client("음악이 들리네.")
    llm = InformationPipeline(settings(tmp_path), client=fake)
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    token = CURRENT_AUDIO_INPUTS.set((object(),))
    try:
        await llm.answer(
            store, scope, "사용자",
            "히나야 https://example.com/docs 이 소리 어때?",
            channel_context=[],
        )
        request = fake.responses.create.await_args.kwargs
        assert {"type": "code_execution"} not in request.get("tools", [])
        assert {"type": "url_context"} in request.get("tools", [])
        assert any(
            tool.get("name") == "request_computation" for tool in request.get("tools", [])
        )
        assert fake.responses.create.await_count == 1
        assert CODE_EXECUTION_POLICY not in request["instructions"]
    finally:
        CURRENT_AUDIO_INPUTS.reset(token)
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_gemini_without_audio_keeps_code_execution_after_audio_turn(tmp_path):
    fake = client("알겠어.")
    llm = InformationPipeline(settings(tmp_path), client=fake)
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        await llm.answer(store, scope, "사용자", "히나야 안녕", channel_context=[])
        request = fake.responses.create.await_args.kwargs
        assert {"type": "code_execution"} in request["tools"]
        assert CODE_EXECUTION_POLICY in request["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_audio_delegates_only_on_function_call_and_resumes_answer(tmp_path, monkeypatch):
    fake = client("unused")
    fake.responses.create.side_effect = [
        NS(
            status="requires_action", output_text="", usage=None,
            output=[NS(
                type="function_call", call_id="compute1", name="request_computation",
                arguments='{"task":"compute 6*7","input_data":""}',
            )],
        ),
        NS(
            status="completed", output_text="42", usage=None,
            output=[
                NS(type="code_execution_call", id="code1"),
                NS(type="code_execution_result", call_id="code1", result="42", is_error=False),
            ],
        ),
        NS(status="completed", output_text="42야.", usage=None, output=[NS(type="message")]),
    ]
    config = replace(
        settings(tmp_path),
        model_routing_mode="adaptive",
        model="gemini-smart",
        smart_model="gemini-smart",
        fast_model="gemini-fast",
        gemini_fast_thinking_level="minimal",
        gemini_smart_thinking_level="medium",
    )
    plan = replace(fixed_model_plan(config), thinking_level="medium")
    monkeypatch.setattr(
        "hina_bot.ai.information_pipeline.build_model_plan", lambda *args, **kwargs: plan,
    )
    llm = InformationPipeline(config, client=fake)
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    token = CURRENT_AUDIO_INPUTS.set((object(),))
    try:
        answer = await llm.answer(store, scope, "사용자", "6*7 계산해 줘", channel_context=[])
        assert answer == "42야."
        assert fake.responses.create.await_count == 3
        first, worker, continuation = [
            entry.kwargs for entry in fake.responses.create.await_args_list
        ]
        assert {"type": "code_execution"} not in first["tools"]
        assert worker["tools"] == [{"type": "code_execution"}]
        assert first["model"] == continuation["model"] == "gemini-smart"
        assert first["thinking_level"] == continuation["thinking_level"] == "medium"
        assert worker["model"] == "gemini-fast"
        assert worker["thinking_level"] == "minimal"
        assert worker["max_output_tokens"] == 4096
        assert isinstance(worker["input"], str)
        assert worker["store"] is False
        assert {"type": "code_execution"} not in continuation["tools"]
        outputs = [
            item for item in continuation["input"]
            if item.get("type") == "function_call_output"
        ]
        assert len(outputs) == 1
        assert outputs[0]["call_id"] == "compute1"
        assert '"executed":true' in outputs[0]["output"]
    finally:
        CURRENT_AUDIO_INPUTS.reset(token)
        await llm.close()
        store.close()
