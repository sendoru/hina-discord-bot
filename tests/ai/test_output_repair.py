"""Regression coverage for optional format-only output repair."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.output_repair import OutputRepair, _acceptable, needs_output_repair
from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store


class Usage:
    def __init__(self, *, reply="고마워"):
        self.events = []
        self.calls = []
        self.reply = reply
        self.error = None

    async def request(self, client, operation, **kwargs):
        self.calls.append((client, operation, kwargs))
        if self.error is not None:
            raise self.error
        return NS(status="completed", output_text=self.reply)

    def routing_event(self, operation, **metadata):
        self.events.append((operation, metadata))


def make_repair(*, mode="active", reply="좋아", model="auto", provider="gemini"):
    settings = Settings(
        discord_token="bot-token", provider=provider,
        output_repair_mode=mode, output_repair_model=model,
        fast_model="fast-llm", model="normal-llm",
    )
    client = NS(provider_name=provider, responses=NS(create=AsyncMock()))
    vision = NS(_client=client)
    usage = Usage(reply=reply)
    return OutputRepair(settings, vision, usage), client, usage


def test_preflight_only_routes_probable_syntax_errors():
    assert needs_output_repair("좋아 [1]")
    assert needs_output_repair("좋아[18:1495269226938630315]")
    assert needs_output_repair(r"안녕\n다음 줄")
    assert not needs_output_repair("좋아! :hina:")
    assert not needs_output_repair("선택지는 [가] 또는 [나]")
    assert not needs_output_repair("`[1]` and ```txt\n[5]\n```")


def test_strict_change_gate_allows_simple_correction_not_paraphrase():
    assert _acceptable("좋아 [1]", "좋아")
    assert _acceptable(r"안녕\n다음 줄", "안녕\n다음 줄".replace("\\n", "\n"))
    assert not _acceptable("좋아 [1]", "")
    assert not _acceptable("좋아 [1]", "마음에 드는구나")
    assert not _acceptable("좋아 [1]", "좋아 " + "!" * 50)


@pytest.mark.asyncio
async def test_active_uses_fast_model_no_original_context_or_images():
    repair, client, usage = make_repair(reply="좋아")
    assert await repair.apply("좋아 [1]") == "좋아"
    assert len(usage.calls) == 1
    used_client, operation, kwargs = usage.calls[0]
    assert used_client is client  # Bypass VisionClient: never re-send images.
    assert operation == "output_repair"
    assert kwargs["model"] == "fast-llm"
    assert kwargs["input"] == "좋아 [1]"
    assert kwargs["thinking_level"] == "minimal"
    assert kwargs["store"] is False
    assert "tools" not in kwargs
    assert kwargs["deadline_seconds"] <= 3
    assert usage.events[-1][1]["status"] == "changed"
    assert "좋아" not in str(usage.events)
    await repair.close()


@pytest.mark.asyncio
async def test_off_or_healthy_output_skips_second_request():
    repair, _, usage = make_repair(mode="off")
    assert await repair.apply("안녕 [1]") == "안녕 [1]"
    assert usage.calls == []
    repair.settings = Settings(
        discord_token="token", output_repair_mode="active",
    )
    assert await repair.apply("평범한 인사.") == "평범한 인사."
    assert usage.calls == []
    await repair.close()


@pytest.mark.asyncio
async def test_shadow_does_not_change_answer_and_emits_comparison():
    repair, _, usage = make_repair(mode="shadow", reply="좋아")
    assert await repair.apply("좋아 [1]") == "좋아 [1]"
    await repair.close()
    assert len(usage.calls) == 1
    assert usage.events[-1][1]["output_repair_changed"] is True
    assert usage.events[-1][1]["output_repair_mode"] == "shadow"


@pytest.mark.asyncio
async def test_failure_and_large_rewrite_fall_back_to_original():
    repair, _, usage = make_repair(reply="딴소리만 잔뜩 하는군요")
    assert await repair.apply("좋아 [1]") == "좋아 [1]"
    assert usage.events[-1][1]["status"] == "rejected"
    usage.error = TimeoutError("sensitive-provider-message")
    assert await repair.apply("좋아 [1]") == "좋아 [1]"
    assert usage.events[-1][1]["status"] == "failed"
    assert "sensitive-provider-message" not in str(usage.events)
    await repair.close()


@pytest.mark.asyncio
async def test_runtime_settings_can_change_mode_and_model_without_restart():
    store = Store(":memory:")
    settings = RuntimeSettings(
        Settings(discord_token="token", provider="gemini", fast_model="default-fast"),
        store,
    )
    repair_client = NS(provider_name="gemini", responses=NS(create=AsyncMock()))
    usage = Usage(reply="좋아")
    repair = OutputRepair(settings, repair_client, usage)
    try:
        assert settings.output_repair_mode == "off"
        assert settings.output_repair_model == "auto"
        assert await repair.apply("좋아 [1]") == "좋아 [1]"
        settings.set_text("output_repair_mode", "active")
        settings.set_text("output_repair_model", "chosen-model")
        assert await repair.apply("좋아 [1]") == "좋아"
        assert usage.calls[0][2]["model"] == "chosen-model"
        with pytest.raises(ValueError):
            settings.set_text("output_repair_mode", "invalid")
        assert settings.output_repair_mode == "active"
        settings.reset("output_repair_mode")
        assert settings.output_repair_mode == "off"
    finally:
        await repair.close()
        store.close()


@pytest.mark.asyncio
async def test_shadow_backpressure_sends_original_immediately():
    repair, _, usage = make_repair(mode="shadow")
    gate = asyncio.Event()

    async def blocked(*args, **kwargs):
        await gate.wait()
        return NS(status="completed", output_text="좋아")

    usage.request = blocked
    try:
        for _ in range(3):
            assert await repair.apply("좋아 [1]") == "좋아 [1]"
        assert usage.events[-1][1]["status"] == "backpressure"
        assert len(repair._tasks) == 2
    finally:
        gate.set()
        await repair.close()


@pytest.mark.asyncio
async def test_answer_pipeline_applies_repair_before_persisted_discord_reply():
    """Integration: two provider requests, only corrected text returned to bot."""
    import json

    import httpx
    from openai import AsyncOpenAI

    from hina_bot.ai.runtime_llm import LLM
    from hina_bot.core.routing import Scope

    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        text = "좋아 [1]" if len(requests) == 1 else "좋아"
        return httpx.Response(200, json={
            "id": f"resp_{len(requests)}",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": body["model"],
            "output": [{
                "id": "msg_1", "type": "message", "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text", "text": text, "annotations": [],
                }],
            }],
        })

    client = AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    settings = Settings(
        discord_token="token", openai_api_key="test", provider="openai",
        model="answer-model", fast_model="small-model",
        output_repair_mode="active", output_repair_model="auto",
        usage_log_path="", chat_web_search=False,
    )
    llm = LLM(settings, client=client)
    store = Store(":memory:")
    try:
        answer = await llm.answer(store, Scope(None, 10, 100), "사용자", "히나야 반가워")
        assert answer == "좋아"
        assert len(requests) == 2
        assert requests[0]["model"] == "answer-model"
        assert requests[1]["model"] == "small-model"
        assert requests[1]["input"] == "좋아 [1]"
        assert "tools" not in requests[1]
        assert "current_speaker" not in str(requests[1])
        assert "lore_reference" not in str(requests[1])
    finally:
        await llm.close()
        store.close()
