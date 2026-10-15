"""End-to-end proof that full mode bypasses static retrieval and keeps runtime scope."""
from tests.reference_blocks import read_reference_context


import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


@pytest.mark.asyncio
async def test_full_mode_includes_static_prefix_and_dynamic_refs_without_embedding(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_full", "object": "response", "created_at": 0,
            "status": "completed", "model": "test-model",
            "output": [{
                "type": "message", "id": "msg_full", "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "응.", "annotations": []}],
            }],
        })

    usage_path = tmp_path / "usage.jsonl"
    client = AsyncOpenAI(api_key="test-key",
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    llm = LLM(Settings(
        discord_token="test", openai_api_key="test", model="test-model",
        db_path=str(tmp_path / "hina.sqlite3"), usage_log_path=str(usage_path),
        chat_web_search=False, rag_mode="full",
    ), client=client)

    def never_run_legacy(_query):
        raise AssertionError("Static retrieval was called in full mode")

    llm.lore_references = never_run_legacy
    llm.retrieval_v2.retrieve = never_run_legacy
    llm.runtime_lore.add("test.dynamic", "테스트 전용 동적 지식",
                         "히나,동적", "히나", "self")
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "히나 동적 설정 알려줘")
        payload = calls[-1]
        instructions = payload["instructions"]
        assert "[검수된 정적 설정 자료" in instructions
        assert "소라사키 히나는 게헨나 학원 3학년" in instructions
        # Audience-only material must not become Hina's supposed memory.
        assert "아코는 과로한 히나에게 휴식이 필요하다고 판단해" not in instructions
        context = read_reference_context(payload["input"][0]["content"])
        assert any("테스트 전용 동적 지식" in str(ref)
                   for ref in context["lore_reference"])
        rows = [json.loads(line) for line in usage_path.read_text().splitlines()]
        rag = next(row for row in rows if row.get("operation") == "rag.full")
        size = next(row for row in rows if row.get("operation") == "context.size")
        mode = next(row for row in rows if row.get("operation") == "rag.mode")
        assert mode["rag_mode"] == mode["rag_selected_context"] == "full"
        assert rag["rag_full_items"] > 100
        assert rag["rag_full_audience_excluded"] >= 1
        assert size["instruction_lore_chars"] == rag["rag_full_chars"]
        assert size["instruction_lore_chars"] > 0
    finally:
        await llm.close()
        store.close()
