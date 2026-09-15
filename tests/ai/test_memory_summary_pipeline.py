import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.memory_summary import SUMMARY_POLICY, MemorySummaryMixin


class FakeStore:
    def __init__(self):
        self.saved = None

    def pending(self, scope):
        return [
            {
                "id": index + 1,
                "created_at": "2026-09-15 00:00:00",
                "content": "새로운 중요한 정보 " * 80,
                "reply": "확인했어 " * 20,
            }
            for index in range(8)
        ]

    def summary(self, scope):
        return "기존 장기 기억 " * 170, 0

    def save_summary(self, scope, text, through):
        self.saved = (text, through)


class ReplyHeavyStore(FakeStore):
    def pending(self, scope):
        return [
            {
                "id": index + 1,
                "created_at": "2026-09-15 00:00:00",
                "content": "짧은 사용자 발화",
                "reply": "긴 봇 답변 " * 100,
            }
            for index in range(8)
        ]

    def summary(self, scope):
        return "기" * 1700, 0


def _pipeline(output_text: str):
    pipeline = object.__new__(InformationPipeline)
    pipeline.settings = NS(
        summary_every=8,
        model="fixed-model",
        model_routing_mode="adaptive",
        memory_routing_smart_threshold=2.0,
        fast_model="fast-model",
        smart_model="smart-model",
        memory_output_tokens=4096,
        gemini_thinking_level="low",
        gemini_fast_thinking_level="minimal",
        gemini_smart_thinking_level="medium",
        provider="openai",
    )
    pipeline.client = object()
    pipeline.usage = NS(request=AsyncMock(return_value=NS(
        status="completed",
        output_text=output_text,
    )))
    return pipeline


def test_information_pipeline_uses_shared_memory_summary_mixin():
    assert InformationPipeline.summarize is MemorySummaryMixin.summarize
    assert InformationPipeline.summarize_shared is MemorySummaryMixin.summarize_shared


@pytest.mark.asyncio
async def test_information_pipeline_memory_summary_uses_adaptive_smart_route():
    pipeline = _pipeline("z" * 2500)
    store = FakeStore()
    scope = NS(guild_id=None, user_id=100)

    await pipeline.summarize(store, scope)

    request = pipeline.usage.request.await_args.kwargs
    assert request["model"] == "smart-model"
    assert request["instructions"] == SUMMARY_POLICY
    assert request["max_output_tokens"] == 4096
    assert request["route_metadata"]["model_tier"] == "smart"
    assert "compaction_pressure" in request["route_metadata"]["model_route_reasons"]
    assert store.saved == ("z" * 2000, 8)


@pytest.mark.asyncio
async def test_server_personal_summary_routes_only_on_fields_sent_to_provider():
    pipeline = _pipeline("갱신된 기억")
    store = ReplyHeavyStore()
    scope = NS(guild_id=10, user_id=100)

    await pipeline.summarize(store, scope)

    request = pipeline.usage.request.await_args.kwargs
    payload = json.loads(request["input"])
    assert request["model"] == "fast-model"
    assert request["route_metadata"]["model_route_policy"] == "memory-v1"
    assert "pending_input_volume" not in request["route_metadata"]["model_route_reasons"]
    assert all("hina" not in turn for turn in payload["new_turns"])
