from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.memory_summary import SHARED_SUMMARY_POLICY, MemorySummaryMixin


class SharedStore:
    def __init__(self):
        self.saved = None

    def pending_shared(self, scope):
        return [
            {
                "id": index + 1,
                "created_at": "2026-09-15 00:00:00",
                "name": "사용자",
                "content": "공개 직접 호출의 중요한 정보 " * 80,
            }
            for index in range(8)
        ]

    def shared_summary(self, scope):
        return "기존 공개 기억 " * 120, 0

    def save_shared_summary(self, scope, name, text, through):
        self.saved = (name, text[:1500], through)


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


def test_information_pipeline_keeps_only_shared_summary_generation():
    assert "summarize" not in MemorySummaryMixin.__dict__
    assert InformationPipeline.summarize_shared is MemorySummaryMixin.summarize_shared


@pytest.mark.asyncio
async def test_shared_summary_uses_adaptive_memory_route():
    pipeline = _pipeline("z" * 1800)
    store = SharedStore()
    scope = NS(guild_id=10, user_id=100)

    await pipeline.summarize_shared(store, scope)

    request = pipeline.usage.request.await_args.kwargs
    metadata = request["route_metadata"]
    assert request["model"] == "smart-model"
    assert request["instructions"] == SHARED_SUMMARY_POLICY
    assert request["max_output_tokens"] == 4096
    assert metadata["model_tier"] == "smart"
    assert metadata["model_route_policy"] == "memory-v2"
    assert set(metadata["model_route_components"]) <= {"capacity_load", "pending_load"}
    assert store.saved == ("사용자", "z" * 1500, 8)
