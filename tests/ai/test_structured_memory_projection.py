import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.structured_memory_context import (
    aggregate_relationship_evidence,
    full_relationship_memory,
    owner_dm_memory,
)
from hina_bot.core.config import Settings
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


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


def _client(calls):
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    return AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _reference(call):
    return json.loads(call["input"][0]["content"].split("\n", 1)[1])


def test_owner_dm_memory_contains_every_owner_item_and_no_other_user_item():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    public = Scope(1, 20, 100, True)
    private = Scope(1, 30, 100, False)
    other_guild = Scope(2, 40, 100, True)
    other_user = Scope(1, 20, 200, True)
    for scope, content, disclosure in (
        (public, "PUBLIC_LOCAL", MemoryDisclosure.LOCAL),
        (private, "PRIVATE_IMPLICIT", MemoryDisclosure.IMPLICIT),
        (other_guild, "OTHER_GUILD_GATED", MemoryDisclosure.REFERENCE_GATED),
        (dm, "DM_GLOBAL", MemoryDisclosure.GLOBAL),
    ):
        store.add_memory_item(
            scope,
            content,
            kind=MemoryKind.FACT,
            disclosure=disclosure,
            confidence=0.9,
        )
    store.add_memory_item(
        other_guild,
        "RELATIONSHIP_WITH_EVIDENCE",
        kind=MemoryKind.RELATIONSHIP,
        disclosure=MemoryDisclosure.IMPLICIT,
        confidence=0.95,
        relationship_evidence={"familiarity": 3, "comfort": 2},
    )
    store.add_memory_item(
        other_user,
        "OTHER_USER_SECRET",
        kind=MemoryKind.FACT,
        disclosure=MemoryDisclosure.GLOBAL,
    )

    projected = owner_dm_memory(store.memory_items(100), dm)

    assert {row["content"] for row in projected} == {
        "PUBLIC_LOCAL",
        "PRIVATE_IMPLICIT",
        "OTHER_GUILD_GATED",
        "DM_GLOBAL",
        "RELATIONSHIP_WITH_EVIDENCE",
    }
    relationship = next(
        row for row in projected if row["content"] == "RELATIONSHIP_WITH_EVIDENCE"
    )
    assert relationship["relationship_evidence"] == {
        "familiarity": 3,
        "comfort": 2,
    }
    assert "OTHER_USER_SECRET" not in json.dumps(projected, ensure_ascii=False)
    store.close()


def test_single_cross_space_observation_projects_numeric_profile_without_raw_text():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    store.add_memory_item(
        dm,
        "RAW_RELATIONSHIP_SECRET",
        kind=MemoryKind.RELATIONSHIP,
        disclosure=MemoryDisclosure.IMPLICIT,
        confidence=0.95,
        relationship_evidence={
            "familiarity": 3,
            "comfort": 2,
            "teasing_tolerance": 2,
        },
    )

    projection = aggregate_relationship_evidence(store.memory_items(100), server)

    assert projection == {
        "familiarity": 3,
        "comfort": 2,
        "teasing_tolerance": 2,
    }
    assert "RAW_RELATIONSHIP_SECRET" not in json.dumps(projection, ensure_ascii=False)
    store.close()


def test_repeated_moderate_evidence_accumulates_without_overwriting_global_state():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    for source in ("101", "201"):
        store.add_memory_item(
            dm,
            f"relationship observation {source}",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            confidence=0.9,
            source_message_ids=(source,),
            relationship_evidence={"familiarity": 2},
        )

    projection = aggregate_relationship_evidence(store.memory_items(100), server)

    assert projection == {"familiarity": 3}
    store.close()


def test_missing_axis_is_not_interpreted_as_negative_evidence():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    store.add_memory_item(
        dm,
        "comfortable but no teasing evidence",
        kind=MemoryKind.RELATIONSHIP,
        disclosure=MemoryDisclosure.IMPLICIT,
        confidence=0.95,
        relationship_evidence={"comfort": 3},
    )

    projection = aggregate_relationship_evidence(store.memory_items(100), server)

    assert projection == {"comfort": 3}
    assert "teasing_tolerance" not in projection
    store.close()


def test_low_confidence_relationship_evidence_does_not_project():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    store.add_memory_item(
        dm,
        "weak relationship",
        kind=MemoryKind.RELATIONSHIP,
        disclosure=MemoryDisclosure.IMPLICIT,
        confidence=0.79,
        relationship_evidence={"familiarity": 4},
    )

    assert aggregate_relationship_evidence(store.memory_items(100), server) == {}
    store.close()


def test_superseded_memory_is_excluded_from_owner_projection():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    try:
        old_id = store.add_memory_item(
            dm,
            "OLD_FACT",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        new_id = store.add_memory_item(
            dm,
            "NEW_FACT",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        proposal_id = store.add_memory_reconciliation_proposal(
            dm,
            new_memory_item_id=new_id,
            target_memory_item_id=old_id,
            relation="corrects",
            confidence=0.99,
        )
        assert proposal_id is not None
        assert store.apply_memory_reconciliation_proposal(proposal_id) == "applied"

        projected = owner_dm_memory(store.memory_items(100), dm)

        assert [row["content"] for row in projected] == ["NEW_FACT"]
    finally:
        store.close()


def test_same_disclosure_space_gets_raw_relationship_memory_not_cross_space_profile():
    store = Store(":memory:")
    source = Scope(1, 20, 100, True)
    same_guild = Scope(1, 30, 100, True)
    store.add_memory_item(
        source,
        "SAME_SPACE_RELATIONSHIP",
        kind=MemoryKind.RELATIONSHIP,
        disclosure=MemoryDisclosure.IMPLICIT,
        confidence=0.95,
        relationship_evidence={"familiarity": 3, "casualness": 3},
    )

    raw = full_relationship_memory(store.memory_items(100), same_guild)
    implicit = aggregate_relationship_evidence(store.memory_items(100), same_guild)

    assert raw == [{
        "kind": "relationship",
        "content": "SAME_SPACE_RELATIONSHIP",
        "confidence": 0.95,
        "relationship_evidence": {"familiarity": 3, "casualness": 3},
    }]
    assert implicit == {}
    store.close()


@pytest.mark.asyncio
async def test_dm_response_receives_all_owner_structured_memory_even_when_legacy_flag_is_off():
    calls = []
    client = _client(calls)
    settings = Settings(
        "test",
        "test",
        external_context_policy="full",
        public_memory_in_dm=False,
    )
    llm = LLM(settings, client=client)
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    public = Scope(1, 20, 100, True)
    private = Scope(1, 30, 100, False)
    other_guild = Scope(2, 40, 100, True)
    other_user = Scope(1, 20, 200, True)
    try:
        store.add_memory_item(
            public,
            "PUBLIC_FACT_MARKER",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        store.add_memory_item(
            private,
            "PRIVATE_FACT_MARKER",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        store.add_memory_item(
            other_guild,
            "OTHER_GUILD_RELATION_MARKER",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            relationship_evidence={"familiarity": 3, "task_orientation": 2},
        )
        store.add_memory_item(
            other_user,
            "OTHER_USER_MARKER",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.GLOBAL,
        )

        await llm.answer(store, dm, "사용자", "안녕", channel_context=[])

        payload = calls[-1]
        reference = _reference(payload)
        raw = json.dumps(reference, ensure_ascii=False)
        assert "PUBLIC_FACT_MARKER" in raw
        assert "PRIVATE_FACT_MARKER" in raw
        assert "OTHER_GUILD_RELATION_MARKER" in raw
        assert "OTHER_USER_MARKER" not in raw
        relationship = next(
            row for row in reference["structured_owner_memory"]
            if row["content"] == "OTHER_GUILD_RELATION_MARKER"
        )
        assert relationship["relationship_evidence"] == {
            "familiarity": 3,
            "task_orientation": 2,
        }
        assert reference["structured_relationship_memory"] == []
        assert reference["cross_space_relationship"] == {}
        assert "구조화 사용자 기억" in payload["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_server_receives_numeric_implicit_profile_without_cross_space_raw_content():
    calls = []
    client = _client(calls)
    llm = LLM(Settings("test", "test", external_context_policy="full"), client=client)
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        store.add_memory_item(
            dm,
            "RAW_RELATIONSHIP_SECRET",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            confidence=0.95,
            relationship_evidence={
                "familiarity": 3,
                "comfort": 2,
                "casualness": 3,
            },
        )
        store.add_memory_item(
            dm,
            "RAW_FACT_SECRET",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
            confidence=0.99,
        )

        await llm.answer(
            store,
            server,
            "사용자",
            "전에 말한 면접 기억나?",
            channel_context=[],
        )

        payload = calls[-1]
        reference = _reference(payload)
        raw = json.dumps(reference, ensure_ascii=False)
        assert reference["structured_owner_memory"] == []
        assert reference["structured_relationship_memory"] == []
        assert reference["cross_space_relationship"] == {
            "familiarity": 3,
            "comfort": 2,
            "casualness": 3,
        }
        assert "RAW_RELATIONSHIP_SECRET" not in raw
        assert "RAW_FACT_SECRET" not in raw
        assert "구조화 사용자 기억" in payload["instructions"]
        assert "1~4" in payload["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_same_space_response_receives_raw_relationship_and_evidence():
    calls = []
    client = _client(calls)
    llm = LLM(Settings("test", "test", external_context_policy="full"), client=client)
    store = Store(":memory:")
    source = Scope(1, 20, 100, True)
    same_guild = Scope(1, 30, 100, True)
    try:
        store.add_memory_item(
            source,
            "SAME_SPACE_RELATIONSHIP",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            confidence=0.95,
            relationship_evidence={"familiarity": 3, "casualness": 3},
        )

        await llm.answer(store, same_guild, "사용자", "안녕", channel_context=[])

        reference = _reference(calls[-1])
        raw = json.dumps(reference, ensure_ascii=False)
        assert "SAME_SPACE_RELATIONSHIP" in raw
        assert reference["structured_relationship_memory"][0]["relationship_evidence"] == {
            "familiarity": 3,
            "casualness": 3,
        }
        assert reference["cross_space_relationship"] == {}
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_current_channel_scope_suppresses_only_cross_space_profile():
    calls = []
    client = _client(calls)
    llm = LLM(Settings("test", "test", external_context_policy="full"), client=client)
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        store.add_memory_item(
            dm,
            "RAW_RELATIONSHIP_SECRET",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            confidence=0.95,
            relationship_evidence={"familiarity": 4},
        )

        await llm.answer(
            store,
            server,
            "사용자",
            "이 채널에서 우리 대화 분위기 어땠어?",
            channel_context=[],
        )

        payload = calls[-1]
        reference = _reference(payload)
        assert reference["cross_space_relationship"] == {}
        assert reference["structured_relationship_memory"] == []
        assert "RAW_RELATIONSHIP_SECRET" not in json.dumps(reference, ensure_ascii=False)
        assert "구조화 사용자 기억" not in payload["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_other_users_relationship_evidence_never_projects():
    calls = []
    client = _client(calls)
    llm = LLM(Settings("test", "test", external_context_policy="full"), client=client)
    store = Store(":memory:")
    other_dm = Scope(None, 11, 200)
    server = Scope(1, 20, 100, True)
    try:
        store.add_memory_item(
            other_dm,
            "OTHER_USER_RELATIONSHIP",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            confidence=0.99,
            relationship_evidence={"familiarity": 4, "comfort": 4},
        )

        await llm.answer(store, server, "사용자", "안녕", channel_context=[])

        payload = calls[-1]
        reference = _reference(payload)
        assert reference["cross_space_relationship"] == {}
        assert reference["structured_relationship_memory"] == []
        assert "OTHER_USER_RELATIONSHIP" not in json.dumps(reference, ensure_ascii=False)
    finally:
        await llm.close()
        store.close()
