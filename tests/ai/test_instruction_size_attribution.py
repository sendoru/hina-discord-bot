import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import LLM
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

_INSTRUCTION_GROUP_FIELDS = (
    "instruction_base_chars",
    "instruction_reference_chars",
    "instruction_identity_chars",
    "instruction_character_chars",
    "instruction_relationship_chars",
    "instruction_runtime_chars",
    "instruction_memory_chars",
    "instruction_context_policy_chars",
    "instruction_search_chars",
    "instruction_world_chars",
    "instruction_tools_chars",
    "instruction_dynamic_chars",
    "instruction_response_chars",
)


def _response():
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "gpt-4.1-mini",
        "output": [{
            "type": "message",
            "id": "msg_test",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "응", "annotations": []}],
        }],
    }


@pytest.mark.asyncio
async def test_instruction_size_telemetry_matches_assembled_request(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    usage_path = tmp_path / "usage.jsonl"
    llm = LLM(
        Settings(
            "test",
            "test",
            usage_log_path=str(usage_path),
            chat_web_search=False,
        ),
        client=client,
    )
    store = Store(":memory:")
    try:
        await llm.answer(
            store,
            Scope(None, 10, 100),
            "사용자",
            "안녕",
            use_memory=False,
        )
    finally:
        await llm.close()
        store.close()

    payload = calls[-1]
    rows = [json.loads(line) for line in usage_path.read_text().splitlines()]
    size = next(row for row in rows if row.get("operation") == "context.size")

    assert len(payload["instructions"]) == size["instruction_chars"]
    assert (
        sum(size[field] for field in _INSTRUCTION_GROUP_FIELDS)
        + size["instruction_separator_chars"]
        == size["instruction_chars"]
    )

    serialized_input_chars = len(
        json.dumps(payload["input"], ensure_ascii=False, separators=(",", ":"))
    )
    assert size["request_input_chars"] == serialized_input_chars
    assert size["request_chars_total"] == size["instruction_chars"] + serialized_input_chars

    # Absent optional sections are explicit zeros, not missing telemetry.
    assert size["instruction_memory_chars"] == 0
    assert size["instruction_dynamic_chars"] == 0
