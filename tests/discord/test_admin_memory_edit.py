import asyncio
from types import SimpleNamespace as NS

import pytest

from hina_bot.core.admin_commands import AdminCommand
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.admin_commands import execute_admin_command


@pytest.mark.asyncio
async def test_memory_item_edit_uses_stored_scope_and_memory_lock_only():
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    item_id = store.add_memory_item(
        scope,
        "before",
        kind="preference",
        disclosure="implicit",
        confidence=0.8,
    )
    locks = {}
    acquired = []

    class TrackingLock:
        def __init__(self, key):
            self.key = key
            self.lock = locks.setdefault(key, asyncio.Lock())

        async def __aenter__(self):
            acquired.append(self.key)
            await self.lock.acquire()

        async def __aexit__(self, exc_type, exc, tb):
            self.lock.release()

    def memory_lock(item_scope):
        key = (item_scope.realm, item_scope.channel_id, item_scope.user_id)
        return TrackingLock(key)

    client = NS(store=store, memory_lock=memory_lock)
    command = AdminCommand(
        id=7,
        request_id="a" * 32,
        actor="local-dashboard",
        action="memory.item.edit",
        target=str(item_id),
        payload={
            "item_id": item_id,
            "expected_revision": 0,
            "content": "after",
            "kind": "preference",
            "disclosure": "global",
            "confidence": 0.95,
            "relationship_evidence": {},
            # These must not influence the authoritative scope.
            "guild_id": 999,
            "channel_id": 999,
            "user_id": 999,
        },
    )

    result = await execute_admin_command(client, command)

    assert result == {"item_id": item_id, "revision": 1, "changed": True}
    assert acquired == [("guild:1", 10, 100)]
    revised = store.memory_item(item_id)
    assert revised is not None
    assert revised.user_id == "100"
    assert revised.origin_realm == "guild:1"
    assert revised.origin_channel_id == "10"
    assert revised.content == "after"
    assert revised.revision == 1
    assert store.memory_item_edit_history(item_id)[0]["admin_command_id"] == 7
    store.close()


@pytest.mark.asyncio
async def test_memory_item_edit_rejects_stale_revision():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    item_id = store.add_memory_item(scope, "before", kind="fact", disclosure="local")
    store.revise_memory_item(
        item_id,
        expected_revision=0,
        content="first edit",
        kind="fact",
        disclosure="local",
        confidence=1,
    )
    lock = asyncio.Lock()
    client = NS(store=store, memory_lock=lambda _: lock)

    with pytest.raises(ValueError, match="changed"):
        await execute_admin_command(
            client,
            AdminCommand(
                id=8,
                request_id="b" * 32,
                actor="local-dashboard",
                action="memory.item.edit",
                target=str(item_id),
                payload={
                    "item_id": item_id,
                    "expected_revision": 0,
                    "content": "stale edit",
                    "kind": "fact",
                    "disclosure": "local",
                    "confidence": 1,
                    "relationship_evidence": {},
                },
            ),
        )
    store.close()


@pytest.mark.asyncio
async def test_memory_item_retract_uses_stored_scope_and_memory_lock_only():
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    item_id = store.add_memory_item(
        scope,
        "retract me",
        kind="fact",
        disclosure="local",
    )
    acquired = []
    lock = asyncio.Lock()

    class TrackingLock:
        async def __aenter__(self):
            acquired.append(("guild:1", 10, 100))
            await lock.acquire()

        async def __aexit__(self, exc_type, exc, tb):
            lock.release()

    client = NS(store=store, memory_lock=lambda _: TrackingLock())
    result = await execute_admin_command(
        client,
        AdminCommand(
            id=9,
            request_id="c" * 32,
            actor="local-dashboard",
            action="memory.item.retract",
            target=str(item_id),
            payload={
                "item_id": item_id,
                "expected_revision": 0,
                "guild_id": 999,
                "channel_id": 999,
                "user_id": 999,
            },
        ),
    )

    assert result == {"item_id": item_id, "revision": 1, "status": "retracted"}
    assert acquired == [("guild:1", 10, 100)]
    retracted = store.memory_item(item_id)
    assert retracted is not None
    assert retracted.status.value == "retracted"
    assert retracted.revision == 1
    store.close()
