"""Gemini storage opt-in applies to every memory request, not only answers."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.core.config import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "enabled", "expected"),
    [
        ("gemini", False, False),
        ("gemini", True, True),
        ("openai", True, False),
    ],
)
@pytest.mark.parametrize(
    "operation", ["extract_memory_items_shadow", "summarize_shared"],
)
async def test_memory_requests_respect_global_gemini_store(
    provider, enabled, expected, operation,
):
    worker = MemorySummaryMixin()
    worker.settings = Settings(
        discord_token="token", provider=provider,
        gemini_store_interactions=enabled,
    )
    worker.client = object()
    worker.usage = NS(request=AsyncMock(return_value=NS(status="completed")))
    plan = NS(
        model="memory-model", max_output_tokens=123,
        thinking_level="minimal", telemetry=lambda: {},
    )

    await worker._memory_request(
        operation, "summarize the conversation", {"turns": ["hello"]}, plan,
    )

    args, kwargs = worker.usage.request.await_args
    assert args == (worker.client, operation)
    assert kwargs["store"] is expected
    assert kwargs["input"] == '{"turns":["hello"]}'
