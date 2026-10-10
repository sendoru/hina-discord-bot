import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def _response():
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "test-model",
        "output": [{
            "type": "message",
            "id": "msg_test",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "응.", "annotations": []}],
        }],
    }


@pytest.mark.asyncio
async def test_active_v2_profile_replaces_legacy_context_without_semantic_calibration(tmp_path):
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
            discord_token="test",
            openai_api_key="test",
            model="test-model",
            db_path=str(tmp_path / "hina.sqlite3"),
            usage_log_path=str(usage_path),
            chat_web_search=False,
            retrieval_v2_mode="active",
        ),
        client=client,
    )
    store = Store(":memory:")
    try:
        await llm.answer(
            store,
            Scope(None, 20, 100),
            "사용자",
            "히나야 생일 언제야?",
            use_memory=False,
        )
    finally:
        await llm.close()
        store.close()

    payload = calls[-1]
    context = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
    references = [row.get("reference") for row in context["lore_reference"]]
    assert "canon.hina.birthday" in references
    assert context["character_insights"] == []
    assert context["optional_reactions"] == []

    usage = [json.loads(line) for line in usage_path.read_text().splitlines()]
    event = next(row for row in usage if row.get("operation") == "retrieval_v2.active")
    assert event["status"] == "completed"
    assert event["retrieval_v2_mode"] == "active"
    assert event["retrieval_v2_calibration_status"] == "embedding_backend_unavailable"
    assert event["retrieval_v2_embedding_requests"] == 0


@pytest.mark.asyncio
async def test_shadow_v2_does_not_change_answer_context_or_wait_for_semantics(tmp_path):
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
            discord_token="test",
            openai_api_key="test",
            model="test-model",
            db_path=str(tmp_path / "hina.sqlite3"),
            usage_log_path=str(usage_path),
            chat_web_search=False,
            retrieval_v2_mode="shadow",
        ),
        client=client,
    )
    store = Store(":memory:")
    try:
        await llm.answer(
            store,
            Scope(None, 20, 100),
            "사용자",
            "히나야 생일 언제야?",
            use_memory=False,
        )
    finally:
        # close waits for any already-started shadow job after the answer has completed.
        await llm.close()
        store.close()

    payload = calls[-1]
    context = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
    assert "canon.hina.birthday" in [
        row.get("reference") for row in context["lore_reference"]
    ]
    # Shadow never changes the legacy request shape.
    assert "character_insights" not in context
    assert "optional_reactions" not in context

    usage = [json.loads(line) for line in usage_path.read_text().splitlines()]
    event = next(row for row in usage if row.get("operation") == "retrieval_v2.shadow")
    assert event["status"] == "completed"
    assert event["retrieval_v2_mode"] == "shadow"
