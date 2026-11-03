"""Real files: every deletion path removes the photos waiting for extraction (#472).

The recording-store matrix in ``test_pending_visual_deletion`` pins which call each path
makes. These tests check that the call really removes files, and that what a path should
leave alone stays.
"""

import pytest

from hina_bot.core.pending_visuals import (
    DiskPendingVisualStore,
    MemoryVisual,
    NullPendingVisualStore,
    pending_visual_root,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.reset_analysis_data import reset_analysis_data
from hina_bot.tooling.reset_conversation import reset_conversation

PNG = b"\x89PNG\r\n\x1a\n" + b"photo"

GUILD_A = Scope(1, 10, 100, True)        # the scope each path acts on
SAME_CHANNEL_OTHER_USER = Scope(1, 10, 200, True)
SAME_USER_OTHER_CHANNEL = Scope(1, 20, 100, True)
OTHER_GUILD = Scope(2, 10, 100, True)
DM = Scope(None, 30, 100)
ALL_SCOPES = [GUILD_A, SAME_CHANNEL_OTHER_USER, SAME_USER_OTHER_CHANNEL, OTHER_GUILD, DM]


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store.create(str(path))
    yield store, path
    store.close()


def message_id(scope: Scope) -> int:
    return ALL_SCOPES.index(scope) + 1


def hold(store: Store, scope: Scope) -> None:
    """A stored turn with a photo still waiting for extraction."""
    store.add(scope, message_id(scope), "우리 애 미용했어", "오, 어땠어?")
    stored = store.pending_visuals.put(
        scope.realm, str(scope.channel_id), str(scope.user_id), str(message_id(scope)),
        [MemoryVisual(str(message_id(scope)), 1, "image/png", PNG)],
    )
    assert stored == 1


def holding(store: Store) -> set[Scope]:
    return {
        scope for scope in ALL_SCOPES
        if store.pending_visuals.get(
            scope.realm, str(scope.channel_id), str(scope.user_id), [str(message_id(scope))]
        )
    }


def seed_all(store: Store) -> None:
    for scope in ALL_SCOPES:
        hold(store, scope)
    assert holding(store) == set(ALL_SCOPES)


def test_a_file_backed_store_keeps_photos_beside_its_database(env):
    store, path = env
    assert isinstance(store.pending_visuals, DiskPendingVisualStore)
    assert store.pending_visuals.root == pending_visual_root(path)
    hold(store, GUILD_A)
    assert (path.parent / "visual_pending").is_dir()


def test_an_in_memory_store_keeps_nothing():
    store = Store.create(":memory:")
    try:
        assert isinstance(store.pending_visuals, NullPendingVisualStore)
    finally:
        store.close()


def test_forget_removes_only_that_users_photos_in_that_realm(env):
    store, _ = env
    seed_all(store)

    store.forget(GUILD_A)

    assert holding(store) == {SAME_CHANNEL_OTHER_USER, OTHER_GUILD, DM}


def test_channel_purge_removes_every_users_photos_in_the_channel(env):
    store, _ = env
    seed_all(store)

    store.purge_channel_memory(GUILD_A)

    assert holding(store) == {SAME_USER_OTHER_CHANNEL, OTHER_GUILD, DM}


def test_realm_purge_removes_the_realm_but_not_other_realms(env):
    store, _ = env
    seed_all(store)

    store.purge_realm_memory(GUILD_A)

    assert holding(store) == {OTHER_GUILD, DM}


def test_global_purge_removes_every_photo(env):
    store, path = env
    seed_all(store)

    store.purge_all_memory()

    assert holding(store) == set()
    assert list((path.parent / "visual_pending").iterdir()) == []


def test_deleting_a_message_removes_only_that_turns_photo(env):
    store, _ = env
    seed_all(store)

    store.discard_pending_visuals_for_message(GUILD_A.channel_id, message_id(GUILD_A))

    assert holding(store) == set(ALL_SCOPES) - {GUILD_A}


def test_the_same_message_id_in_another_channel_is_not_touched(env):
    store, _ = env
    seed_all(store)

    store.discard_pending_visuals_for_message(999, message_id(GUILD_A))

    assert holding(store) == set(ALL_SCOPES)


def test_pending_visual_keys_cover_only_turns_still_waiting(env):
    store, _ = env
    for scope in (GUILD_A, DM):
        hold(store, scope)
        hold_second = message_id(scope) + 100
        store.add(scope, hold_second, "다음 말", "응")
    store.save_memory_extraction_cursor(GUILD_A, store.history(GUILD_A)[0]["id"])

    assert store.pending_visual_keys() == {
        ("guild:1", "10", "100", str(message_id(GUILD_A) + 100)),
        ("dm:100", "30", "100", str(message_id(DM))),
        ("dm:100", "30", "100", str(message_id(DM) + 100)),
    }


def test_the_sweep_drops_photos_whose_turn_was_extracted_or_is_gone(env):
    store, _ = env
    seed_all(store)
    store.save_memory_extraction_cursor(GUILD_A, store.history(GUILD_A)[-1]["id"])
    with store.db:
        store.db.execute("DELETE FROM turns WHERE scope=?", (DM.conversation,))

    removed = store.pending_visuals.sweep(store.pending_visual_keys(), max_age_seconds=3600)

    assert removed == 2
    assert holding(store) == {SAME_CHANNEL_OTHER_USER, SAME_USER_OTHER_CHANNEL, OTHER_GUILD}


# -- offline tools ------------------------------------------------------------------------


def test_the_conversation_reset_clears_photos_with_the_turns_it_deletes(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store.create(str(path))
    seed_all(store)
    store.close()

    reset_conversation(
        path, backup_dir=tmp_path / "backups", offline_confirmed=True, discard_pending=True
    )

    reopened = Store.create(str(path))
    try:
        assert holding(reopened) == set()
        assert list(pending_visual_root(path).iterdir()) == []
    finally:
        reopened.close()


def test_the_analysis_reset_clears_photos_left_by_extracted_turns(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store.create(str(path))
    for scope in ALL_SCOPES:
        hold(store, scope)
        store.save_memory_extraction_cursor(scope, store.history(scope)[-1]["id"])
    store.close()

    reset_analysis_data(path, usage_log_path=str(tmp_path / "u"), event_log_path=str(tmp_path / "e"),
                        apply=True)

    reopened = Store.create(str(path))
    try:
        assert holding(reopened) == set()
    finally:
        reopened.close()


def test_an_analysis_reset_dry_run_keeps_the_photos(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store.create(str(path))
    hold(store, GUILD_A)
    store.save_memory_extraction_cursor(GUILD_A, store.history(GUILD_A)[-1]["id"])
    store.close()

    reset_analysis_data(path, usage_log_path=str(tmp_path / "u"), event_log_path=str(tmp_path / "e"),
                        apply=False)

    reopened = Store.create(str(path))
    try:
        assert holding(reopened) == {GUILD_A}
    finally:
        reopened.close()
