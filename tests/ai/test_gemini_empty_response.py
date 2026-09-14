import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from hina_bot.usage import EmptyProviderResponseError, UsageLogger


def _response(text: str, *, input_tokens: int, output_tokens: int, reasoning_tokens: int = 0):
    return NS(
        status="completed",
        output_text=text,
        output=[],
        usage=NS(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens + reasoning_tokens,
            input_tokens_details=NS(cached_tokens=0),
            output_tokens_details=NS(reasoning_tokens=reasoning_tokens),
        ),
    )


@pytest.mark.asyncio
async def test_gemini_completed_empty_response_retries_once(tmp_path):
    path = tmp_path / "usage.jsonl"
    logger = UsageLogger(str(path))
    first = _response("", input_tokens=100, output_tokens=0, reasoning_tokens=10)
    second = _response("정상 답변", input_tokens=100, output_tokens=5, reasoning_tokens=4)
    create = AsyncMock(side_effect=[first, second])
    client = NS(provider_name="gemini", responses=NS(create=create))

    result = await logger.request(client, "answer", model="gemini-test", input="question")
    logger.close()

    assert result is second
    assert create.await_count == 2
    row = json.loads(path.read_text())
    assert row["status"] == "completed"
    assert row["api_attempts"] == 2
    assert row["empty_response_retries"] == 1
    assert row["input_tokens"] == 200
    assert row["output_tokens"] == 5
    assert row["reasoning_tokens"] == 14
    assert row["total_tokens"] == 219
    assert row["response_error_codes"] == ["empty_response_retried"]


@pytest.mark.asyncio
async def test_gemini_two_empty_responses_raise_diagnostic_error(tmp_path):
    path = tmp_path / "usage.jsonl"
    logger = UsageLogger(str(path))
    first = _response("", input_tokens=80, output_tokens=0, reasoning_tokens=7)
    second = _response("", input_tokens=80, output_tokens=0, reasoning_tokens=6)
    create = AsyncMock(side_effect=[first, second])
    client = NS(provider_name="gemini", responses=NS(create=create))

    with pytest.raises(EmptyProviderResponseError):
        await logger.request(client, "answer", model="gemini-test", input="question")
    logger.close()

    assert create.await_count == 2
    row = json.loads(path.read_text())
    assert row["status"] == "error"
    assert row["error_type"] == "EmptyProviderResponseError"
    assert row["provider"] == "gemini"
    assert row["provider_response_status"] == "completed"
    assert row["provider_error_code"] == "EMPTY_RESPONSE"
    assert row["api_attempts"] == 2
    assert row["empty_response_retries"] == 1
    assert row["input_tokens"] == 160
    assert row["reasoning_tokens"] == 13


@pytest.mark.asyncio
async def test_non_gemini_empty_response_is_not_retried(tmp_path):
    path = tmp_path / "usage.jsonl"
    logger = UsageLogger(str(path))
    empty = _response("", input_tokens=10, output_tokens=0)
    create = AsyncMock(return_value=empty)
    client = NS(provider_name="openai", responses=NS(create=create))

    result = await logger.request(client, "answer", model="test", input="question")
    logger.close()

    assert result is empty
    assert create.await_count == 1
    row = json.loads(path.read_text())
    assert row["api_attempts"] == 1
    assert "empty_response_retries" not in row
