"""recall_memory: FULL plus this turn's T4 approvals, budgeted and lookup-marked (#457)."""

import json

import pytest

from hina_bot.ai.local_tools import LocalToolError, LocalToolRegistry
from hina_bot.ai.memory_extraction import (
    build_shadow_evidence_context,
    build_shadow_turns,
)
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.server_memory_selection import CURRENT_SERVER_READER
from hina_bot.discord.memory_recall_tool import (
    MEMORY_RECALL_TOOL,
    MemoryRecall,
    register_memory_recall_tool,
)

@pytest.mark.asyncio
async def test_server_tool_uses_request_reader_and_enforces_call_budget():
    async def reader(query, *, lookup):
        assert (query, lookup) == ("등산", True)
        return [{"id": 7, "store": "server"}]

    registry = LocalToolRegistry()
    register_memory_recall_tool(registry)
    assert not registry
    token = CURRENT_SERVER_READER.set(reader)
    try:
        register_memory_recall_tool(registry)
        assert registry
        tool = MemoryRecall()
        assert (await tool({"query": "등산"}))["status"] == "ok"
        assert CURRENT_CONTEXT_PROVENANCE.get()["server_memory"][-1]["tier"] == "S3"
        with pytest.raises(LocalToolError, match="invalid_arguments"):
            await tool({"query": "등산", "scope": "personal"})
        with pytest.raises(LocalToolError, match="budget_exhausted"):
            await tool({"query": "등산"})
    finally:
        CURRENT_SERVER_READER.reset(token)


def test_tool_description_separates_memory_from_chat_history():
    assert "get_chat_history" in MEMORY_RECALL_TOOL.description
    assert MEMORY_RECALL_TOOL.parameters["required"] == ["query"]


def test_memory_lookup_answers_never_reach_extraction():
    marked = {"message_id": "1", "created_at": "2026-10-30 03:00:00", "content": "내 여행 언제였지?",
              "reply": "오사카 여행 간다고 했잖아", "exportable": 0,
              "context_provenance": json.dumps({"memory_lookup": {"calls": 1, "items": []}})}
    plain = {**marked, "message_id": "2", "context_provenance": "{}"}

    turns, *_ = build_shadow_turns([marked, plain], include_replies=True)
    evidence, _ = build_shadow_evidence_context([marked])

    assert "hina" not in turns[0] and turns[1]["hina"] == "오사카 여행 간다고 했잖아"
    assert evidence[0]["hina"] == ""
