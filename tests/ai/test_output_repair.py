"""Regression coverage for optional format-only output repair."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.output_repair import OutputRepair, needs_output_repair
from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store


class Usage:
    def __init__(self, *, reply="고마워"):
        self.events = []
        self.calls = []
        self.reply = reply
        self.status = "completed"
        self.error = None

    async def request(self, client, operation, **kwargs):
        self.calls.append((client, operation, kwargs))
        if self.error is not None:
            raise self.error
        return NS(status=self.status, output_text=self.reply)

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


def test_preflight_only_routes_probable_format_errors():
    assert needs_output_repair("좋아 [1]")
    assert needs_output_repair("좋아[18:1495269226938630315]")
    assert needs_output_repair("당황스러웠어......[1562812225691983904]")
    assert needs_output_repair("출처가 이상해 [12345678]")
    assert needs_output_repair("출처가 이상해 [12345678901234567890]")
    assert needs_output_repair(r"안녕\n다음 줄")
    assert needs_output_repair("정말, 가만히 있<b></b>어주니까")
    assert needs_output_repair("안녕 <strong>친구</strong>")
    assert needs_output_repair('인사 <span class="emphasis">안녕</span>')
    assert needs_output_repair("잘 지내<BR/>")
    assert not needs_output_repair("좋아! :hina:")
    assert not needs_output_repair("선택지는 [가] 또는 [나]")
    assert not needs_output_repair("정상적인 숫자 [1234567]")
    assert not needs_output_repair("범위 밖 숫자 [123456789012345678901]")
    assert not needs_output_repair("숫자 예시는 `[1562812225691983904]`")
    assert not needs_output_repair("```txt\n[1562812225691983904]\n```")
    assert not needs_output_repair("`[1]` and ```txt\n[5]\n```")
    assert not needs_output_repair("HTML 예시는 `<b>안녕</b>`")
    assert not needs_output_repair("```html\n<b>안녕</b>\n```")
    assert not needs_output_repair("<@12345> <@&12345> <:hina:12345> <t:1234567890:F>")
    assert not needs_output_repair("이 값은 <limit>이라는 변수야")


@pytest.mark.asyncio
@pytest.mark.parametrize(("original", "reply", "status"), [
    ("좋아 [1]", "좋아", "changed"),
    (
        "무슨 영문인지 몰라서 당황스러웠어......[1562812225691983904]",
        "무슨 영문인지 몰라서 당황스러웠어......",
        "changed",
    ),
    (
        "문서에서 [1562812225691983904]는 메시지 ID를 뜻해.",
        "문서에서 [1562812225691983904]는 메시지 ID를 뜻해.",
        "unchanged",
    ),
    ("<b>안녕</b> [1]", "안녕", "changed"),
    ("정말, 가만히 있<b></b>어주니까", "정말, 가만히 있어주니까", "changed"),
    ("문서에는 <b>강조</b>라고 적혀 있어.", "문서에는 <b>강조</b>라고 적혀 있어.", "unchanged"),
    (r'코드는 `items[1]`과 `"\n"`이야. [1]',
     r'코드는 `items[1]`과 `"\n"`이야. [1]', "unchanged"),
])
async def test_active_uses_fast_model_no_original_context_or_images(original, reply, status):
    repair, client, usage = make_repair(reply=reply)
    assert await repair.apply(original) == reply
    assert len(usage.calls) == 1
    used_client, operation, kwargs = usage.calls[0]
    assert used_client is client  # Bypass VisionClient: never re-send images.
    assert operation == "output_repair"
    assert kwargs["model"] == "fast-llm"
    assert kwargs["input"] == original
    assert kwargs["thinking_level"] == "minimal"
    assert kwargs["store"] is False
    assert "tools" not in kwargs
    assert kwargs["deadline_seconds"] <= 3
    assert usage.events[-1][1]["status"] == status
    assert original not in str(usage.events)
    assert reply not in str(usage.events)
    await repair.close()


@pytest.mark.asyncio
async def test_off_or_healthy_output_skips_second_request():
    repair, _, usage = make_repair(mode="off")
    assert await repair.apply("안녕 [1]") == "안녕 [1]"
    assert await repair.apply("당황스러웠어......[1562812225691983904]") == (
        "당황스러웠어......[1562812225691983904]"
    )
    assert await repair.apply("가만히 있<b></b>어") == "가만히 있<b></b>어"
    assert usage.calls == []
    repair.settings = Settings(
        discord_token="token", output_repair_mode="active",
    )
    assert await repair.apply("평범한 인사.") == "평범한 인사."
    assert await repair.apply("`<b>코드</b>`") == "`<b>코드</b>`"
    assert await repair.apply("`[1562812225691983904]`") == (
        "`[1562812225691983904]`"
    )
    assert usage.calls == []
    await repair.close()


@pytest.mark.asyncio
async def test_shadow_does_not_change_answer_and_emits_comparison():
    repair, _, usage = make_repair(mode="shadow", reply="좋아")
    assert await repair.apply("가만히 있<b></b>어") == "가만히 있<b></b>어"
    await repair.close()
    assert len(usage.calls) == 1
    assert usage.events[-1][1]["output_repair_changed"] is True
    assert usage.events[-1][1]["output_repair_mode"] == "shadow"


@pytest.mark.asyncio
@pytest.mark.parametrize("original", ["좋아 [1]", "가만히 있<b></b>어"])
async def test_failure_falls_back_to_original(original):
    repair, _, usage = make_repair()
    usage.error = TimeoutError("sensitive-provider-message")
    assert await repair.apply(original) == original
    assert usage.events[-1][1]["status"] == "failed"
    assert "sensitive-provider-message" not in str(usage.events)
    await repair.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("response_status", "reply", "event_status"), [
    ("incomplete", "좋아", "non_completed"),
    ("failed", "좋아", "non_completed"),
    ("completed", "", "empty"),
    ("completed", " \n\t", "empty"),
])
async def test_non_completed_or_empty_repair_preserves_original(
    response_status, reply, event_status,
):
    repair, _, usage = make_repair(reply=reply)
    usage.status = response_status
    assert await repair.apply("좋아 [1]") == "좋아 [1]"
    assert len(usage.calls) == 1
    assert usage.events[-1][1]["status"] == event_status
    await repair.close()


@pytest.mark.asyncio
async def test_runtime_settings_can_change_mode_and_model_without_restart():
    store = Store.create(":memory:")
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
@pytest.mark.parametrize(("raw", "corrected"), [
    ("좋아 [1]", "좋아"),
    ("정말, 가만히 있<b></b>어주니까", "정말, 가만히 있어주니까"),
    (
        "나도 무슨 영문인지 몰라서 당황스러웠어......[1562812225691983904]",
        "나도 무슨 영문인지 몰라서 당황스러웠어......",
    ),
])
async def test_answer_pipeline_applies_repair_before_persisted_discord_reply(raw, corrected):
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
        text = raw if len(requests) == 1 else corrected
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
        usage_log_path="", db_path=":memory:", chat_web_search=False,
    )
    llm = LLM(settings, client=client)
    store = Store.create(":memory:")
    try:
        answer = await llm.answer(store, Scope(None, 10, 100), "사용자", "히나야 반가워")
        assert answer == corrected
        assert len(requests) == 2
        assert requests[0]["model"] == "answer-model"
        assert requests[1]["model"] == "small-model"
        assert requests[1]["input"] == raw
        assert "tools" not in requests[1]
        assert "current_speaker" not in str(requests[1])
        assert "lore_reference" not in str(requests[1])
    finally:
        await llm.close()
        store.close()
