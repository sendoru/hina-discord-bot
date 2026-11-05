"""Every path that removes memory also clears the photos waiting for extraction (#472).

The matrix pins which cleanup call each path makes; the disk store's own tests check that
the call really removes files. A failed cleanup must never undo or fail a committed purge.
"""

import logging
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from hina_bot.core.pending_visuals import (
    MemoryVisual,
    NullPendingVisualStore,
    PendingVisualStore,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.web_bot import HinaClient


class RecordingStore(PendingVisualStore):
    """Records cleanup calls and what the database held at that moment."""

    def __init__(self, store_probe=None):
        self.calls: list[tuple] = []
        self.rows_at_call: list[int] = []
        self.store_probe = store_probe

    def _record(self, *call):
        self.calls.append(call)
        if self.store_probe is not None:
            self.rows_at_call.append(self.store_probe())
        return 0

    def discard_messages(self, realm, channel_id, user_id, message_ids):
        return self._record("discard_messages", realm, channel_id, user_id, list(message_ids))

    def discard_user(self, realm, user_id):
        return self._record("discard_user", realm, user_id)

    def discard_channel(self, realm, channel_id):
        return self._record("discard_channel", realm, channel_id)

    def discard_realm(self, realm):
        return self._record("discard_realm", realm)

    def discard_all(self):
        return self._record("discard_all")


def _seed(store: Store, scope: Scope, message_id: int):
    store.add(scope, message_id, f"message-{message_id}", f"reply-{message_id}")


@pytest.fixture
def store():
    value = Store.create(":memory:")
    yield value
    value.close()


def test_default_store_is_the_null_store_and_stores_nothing(store):
    assert isinstance(store.pending_visuals, NullPendingVisualStore)
    visual = MemoryVisual("1", 1, "image/png", b"\x89PNG\r\n\x1a\n")
    assert store.pending_visuals.put("guild:1", "10", "100", "1", [visual]) == 0
    assert store.pending_visuals.get("guild:1", "10", "100", ["1"]) == {}
    assert store.pending_visuals.discard_all() == 0
    assert store.pending_visuals.sweep(set(), max_age_seconds=1) == 0


def test_forget_clears_that_users_photos_in_the_realm(store):
    recorder = store.pending_visuals = RecordingStore()
    scope = Scope(1, 10, 100, True)
    _seed(store, scope, 1)

    store.forget(scope)

    assert recorder.calls == [("discard_user", "guild:1", "100")]


def test_dm_forget_uses_the_dm_realm(store):
    recorder = store.pending_visuals = RecordingStore()

    store.forget(Scope(None, 30, 100))

    assert recorder.calls == [("discard_user", "dm:100", "100")]


def test_channel_purge_clears_every_users_photos_in_that_channel(store):
    recorder = store.pending_visuals = RecordingStore()
    scope = Scope(1, 10, 100, True)
    _seed(store, scope, 1)

    store.purge_channel_memory(scope)

    assert recorder.calls == [("discard_channel", "guild:1", "10")]


def test_realm_purge_clears_the_whole_realm(store):
    recorder = store.pending_visuals = RecordingStore()

    store.purge_realm_memory(Scope(1, 10, 100, True))

    assert recorder.calls == [("discard_realm", "guild:1")]


def test_global_purge_clears_everything(store):
    recorder = store.pending_visuals = RecordingStore()

    store.purge_all_memory()

    assert recorder.calls == [("discard_all",)]


@pytest.mark.parametrize("operation", ["forget", "purge_channel_memory", "purge_realm_memory"])
def test_photos_are_cleared_only_after_the_database_commit(store, operation):
    scope = Scope(1, 10, 100, True)
    _seed(store, scope, 1)
    recorder = store.pending_visuals = RecordingStore(
        store_probe=lambda: len(store.history(scope))
    )

    getattr(store, operation)(scope)

    assert recorder.rows_at_call == [0]


class ExplodingStore(PendingVisualStore):
    def discard_user(self, *args):
        raise OSError("disk unavailable")

    discard_channel = discard_realm = discard_user

    def discard_all(self):
        raise OSError("disk unavailable")


@pytest.mark.parametrize(
    "operation",
    ["forget", "purge_channel_memory", "purge_realm_memory", "purge_all_memory"],
)
def test_a_cleanup_failure_never_fails_or_undoes_a_committed_purge(store, caplog, operation):
    scope = Scope(1, 10, 100, True)
    _seed(store, scope, 1)
    store.pending_visuals = ExplodingStore()

    with caplog.at_level(logging.WARNING, logger="hina"):
        getattr(store, operation)(*([] if operation == "purge_all_memory" else [scope]))

    assert store.history(scope) == []
    assert "Pending visual cleanup failed (OSError" in caplog.text
    assert "disk unavailable" not in caplog.text


def test_deleting_a_message_clears_that_turns_photos(store):
    recorder = store.pending_visuals = RecordingStore()
    _seed(store, Scope(1, 10, 100, True), 555)

    store.discard_pending_visuals_for_message(10, 555)

    assert recorder.calls == [("discard_messages", "guild:1", "10", "100", ["555"])]


def test_dm_message_delete_resolves_the_dm_realm(store):
    recorder = store.pending_visuals = RecordingStore()
    _seed(store, Scope(None, 30, 100), 556)

    store.discard_pending_visuals_for_message(30, 556)

    assert recorder.calls == [("discard_messages", "dm:100", "30", "100", ["556"])]


def test_a_delete_in_another_channel_or_of_an_unknown_message_does_nothing(store):
    recorder = store.pending_visuals = RecordingStore()
    _seed(store, Scope(1, 10, 100, True), 555)

    store.discard_pending_visuals_for_message(99, 555)
    store.discard_pending_visuals_for_message(10, 404)
    store.discard_pending_visuals_for_message("not-a-channel", 555)

    assert recorder.calls == []


class BrokenDb:
    def execute(self, *args, **kwargs):
        raise sqlite3.OperationalError("database is locked")


def test_a_delete_lookup_failure_is_swallowed(store, monkeypatch, caplog):
    recorder = store.pending_visuals = RecordingStore()
    monkeypatch.setattr(store, "db", BrokenDb())

    with caplog.at_level(logging.WARNING, logger="hina"):
        store.discard_pending_visuals_for_message(10, 555)

    assert recorder.calls == []
    assert "Pending visual lookup failed (OperationalError)" in caplog.text


@pytest.mark.asyncio
async def test_message_delete_events_clear_the_cache_and_pending_photos():
    client = NS(
        visual_cache=NS(invalidate=Mock()),
        store=NS(discard_pending_visuals_for_message=Mock()),
    )

    await HinaClient.on_raw_message_delete(client, NS(channel_id=10, message_id=555))

    client.visual_cache.invalidate.assert_called_once_with(10, 555)
    client.store.discard_pending_visuals_for_message.assert_called_once_with(10, 555)


@pytest.mark.asyncio
async def test_bulk_delete_clears_each_message():
    client = NS(
        visual_cache=NS(invalidate=Mock()),
        store=NS(discard_pending_visuals_for_message=Mock()),
    )

    await HinaClient.on_raw_bulk_message_delete(
        client, NS(channel_id=10, message_ids={1, 2, 3})
    )

    cleared = sorted(call.args for call in client.store.discard_pending_visuals_for_message.call_args_list)
    assert cleared == [(10, 1), (10, 2), (10, 3)]


@pytest.mark.asyncio
async def test_message_edits_do_not_clear_pending_photos():
    client = NS(
        visual_cache=NS(invalidate=Mock()),
        store=NS(discard_pending_visuals_for_message=Mock()),
    )

    await HinaClient.on_raw_message_edit(client, NS(channel_id=10, message_id=555))

    client.visual_cache.invalidate.assert_called_once_with(10, 555)
    client.store.discard_pending_visuals_for_message.assert_not_called()
