"""recall_memory: FULL plus this turn's T4 approvals, budgeted and lookup-marked (#457)."""

import json
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from hina_bot.ai.local_tools import LocalToolError, LocalToolRegistry
from hina_bot.ai.memory_extraction import (
    build_shadow_evidence_context,
    build_shadow_turns,
    parse_annotation,
)
from hina_bot.core.memory_context import CURRENT_AUTHORIZED_MEMORY_IDS, CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import scope_target_key
from hina_bot.core.store import Store
from hina_bot.discord.memory_recall_tool import (
    MEMORY_RECALL_TOOL,
    MemoryRecall,
    register_memory_recall_tool,
)
from hina_bot.discord.turn_provenance import CURRENT_TURN_PROVENANCE

DM = Scope(None, 10, 100)
SERVER = Scope(1, 20, 100, True)
SETTINGS = NS(personal_memory_read_mode="tiered", runtime_timezone="Asia/Seoul")


def _store():
    store = Store.create(":memory:")
    trip = store.add_memory_item(DM, "다음 달 오사카 여행 예정", kind=MemoryKind.EVENT,
                                 disclosure=MemoryDisclosure.REFERENCE_GATED)
    store.upsert_memory_annotation(replace(parse_annotation(
        {"time_scope": "planned", "event_at": "2099-11", "usage_mode": "ambient",
         "keywords": ["여행", "오사카"]}, MemoryKind.EVENT, []), memory_item_id=trip))
    server_fact = store.add_memory_item(SERVER, "주말마다 등산 다님", kind=MemoryKind.FACT,
                                        disclosure=MemoryDisclosure.LOCAL)
    store.add_memory_item(Scope(1, 20, 200, True), "다른 사람의 여행 계획",
                          kind=MemoryKind.FACT, disclosure=MemoryDisclosure.GLOBAL)
    return store, trip, server_fact


@pytest.mark.asyncio
async def test_dm_recall_returns_owner_memory_with_status_and_marks_the_turn():
    store, trip, _ = _store()
    CURRENT_CONTEXT_PROVENANCE.set({})
    CURRENT_TURN_PROVENANCE.set({})
    tool = MemoryRecall(store, DM, SETTINGS)

    result = await tool({"query": "여행"})

    assert [row["text"] for row in result["memories"]] == ["다음 달 오사카 여행 예정"]
    assert result["memories"][0]["status"] == "upcoming"
    assert "다른 사람" not in json.dumps(result, ensure_ascii=False)
    lookup = CURRENT_CONTEXT_PROVENANCE.get()["memory_lookup"]
    assert lookup == {"calls": 1, "items": [{"store": "personal", "item_id": trip}]}
    assert CURRENT_TURN_PROVENANCE.get()["memory_lookup"] == lookup
    store.close()


@pytest.mark.asyncio
async def test_server_recall_needs_this_turns_t4_approval_for_reference_gated_items():
    store, trip, _ = _store()
    CURRENT_CONTEXT_PROVENANCE.set({})

    CURRENT_AUTHORIZED_MEMORY_IDS.set(frozenset())
    hidden = await MemoryRecall(store, SERVER, SETTINGS)({"query": "여행"})
    CURRENT_AUTHORIZED_MEMORY_IDS.set(frozenset({trip}))
    shown = await MemoryRecall(store, SERVER, SETTINGS)({"query": "여행"})

    assert hidden["status"] == "empty"
    assert [row["text"] for row in shown["memories"]] == ["다음 달 오사카 여행 예정"]
    CURRENT_AUTHORIZED_MEMORY_IDS.set(frozenset())
    store.close()


@pytest.mark.asyncio
async def test_budget_arguments_and_disabled_memory_are_refused():
    store, _trip, _ = _store()
    tool = MemoryRecall(store, SERVER, SETTINGS)
    await tool({"query": "등산"})
    await tool({"query": "등산"})

    with pytest.raises(LocalToolError, match="budget_exhausted"):
        await tool({"query": "등산"})
    with pytest.raises(LocalToolError, match="invalid_arguments"):
        await MemoryRecall(store, SERVER, SETTINGS)({"query": "  "})
    store.set_memory_mode_override(scope_target_key(SERVER, "channel"), "off")
    with pytest.raises(LocalToolError, match="memory_disabled"):
        await MemoryRecall(store, SERVER, SETTINGS)({"query": "등산"})
    store.close()


@pytest.mark.parametrize(("mode", "memory_mode", "registered"), [
    ("tiered", "normal", True),
    ("shadow", "normal", False),
    ("legacy", "normal", False),
    ("tiered", "off", False),
])
def test_tool_is_offered_only_on_the_tiered_read_path(mode, memory_mode, registered):
    store, _trip, _ = _store()
    store.set_memory_mode_override(scope_target_key(SERVER, "channel"), memory_mode)
    registry = LocalToolRegistry()

    register_memory_recall_tool(registry, store, SERVER, NS(personal_memory_read_mode=mode))

    assert bool(registry) is registered
    store.close()


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
