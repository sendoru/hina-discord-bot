import json

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.structured_memory_context import (
    implicit_relationship_projection,
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
    }
    assert "OTHER_USER_SECRET" not in json.dumps(projected, ensure_ascii=False)
    store.close()


def test_implicit_projection_is_bounded_and_ignores_raw_text():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    store.add_memory_item(
        dm,
        "RAW_RELATIONSHIP_SECRET",
        kind=MemoryKind.RELATIONSHIP,
        disclosure=MemoryDisclosure.IMPLICIT,
        confidence=0.95,
    )

    projection = implicit_relationship_projection(store.memory_items(100), server)

    assert projection == {"familiarity": "established"}
    assert "RAW_RELATIONSHIP_SECRET" not in json.dumps(projection, ensure_ascii=False)
    store.close()


def test_low_confidence_implicit_relationship_does_not_project():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    store.add_memory_item(
        dm,
        "weak relationship",
        kind=MemoryKind.RELATIONSHIP,
        disclosure=MemoryDisclosure.IMPLICIT,
        confidence=0.79,
    )

    assert implicit_relationship_projection(store.memory_items(100), server) == {}
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
        assert reference["cross_space_relationship"] == {}
        assert "구조화 사용자 기억" in payload["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_server_receives_only_bounded_implicit_signal_from_cross_space_memory():
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
        assert reference["cross_space_relationship"] == {"familiarity": "established"}
        assert "RAW_RELATIONSHIP_SECRET" not in raw
        assert "RAW_FACT_SECRET" not in raw
        assert "구조화 사용자 기억" in payload["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_current_channel_scope_suppresses_cross_space_relationship_projection():
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
        assert "RAW_RELATIONSHIP_SECRET" not in json.dumps(reference, ensure_ascii=False)
        assert "구조화 사용자 기억" not in payload["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_other_users_implicit_memory_never_projects():
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
        )

        await llm.answer(store, server, "사용자", "안녕", channel_context=[])

        payload = calls[-1]
        reference = _reference(payload)
        assert reference["cross_space_relationship"] == {}
        assert "OTHER_USER_RELATIONSHIP" not in json.dumps(reference, ensure_ascii=False)
    finally:
        await llm.close()
        store.close()

