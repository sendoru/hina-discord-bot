"""P4 pre-release storage/consent contracts (no LLM or Discord access)."""

import sqlite3

import pytest

from hina_bot.core.db_schema import SchemaError
from hina_bot.core.routing import Scope
from hina_bot.core.server_memory_repository import ServerSourceRef
from hina_bot.core.store import Store


@pytest.fixture
def store():
    value = Store.create(":memory:")
    yield value
    value.close()


def source(
    store, *, guild=1, channel=10, user=100, message=1001,
    public=True, direct=True, content="이 서버는 매주 모임을 해요",
    reply="응, 다음 주에도 만나자", history_lookup=False,
):
    scope = Scope(guild, channel, user, public)
    provenance = {"source_trigger_kind": "explicit" if direct else "implicit"}
    if history_lookup:
        provenance["history_lookup"] = {"source": ["old"]}
    store.add(
        scope, message, content, reply, context_provenance=provenance,
    )
    return ServerSourceRef(str(message), "user")


def remember(store, guild=1, message=1001, **kwargs):
    return store.add_server_memory(
        guild, content="서버 모임은 매주 열림",
        evidence="매주 모임",
        sources=[ServerSourceRef(str(message), "user")],
        **kwargs,
    )


def visible(store, *, scope=None, channels=(10,), members=(100, 200), related=()):
    return store.server_memory_candidates(
        scope or Scope(1, 10, 200, True),
        readable_channel_ids=channels,
        member_user_ids=members,
        related_user_ids=related,
    )


def test_strict_schema_v3_is_bootstrapped_and_detects_drift(store):
    assert store.db.execute(
        "SELECT version FROM schema_migrations WHERE component='store' ORDER BY version DESC"
    ).fetchone()[0] == 3
    assert {
        row[0] for row in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'server_memory_%'"
        )
    } == {"server_memory_items", "server_memory_sources", "server_memory_opt_outs"}
    with store.db:
        store.db.execute("ALTER TABLE server_memory_sources ADD COLUMN unexpected TEXT")
    from hina_bot.core.db_schema import require_current

    with pytest.raises(SchemaError):
        require_current(store.db, ("store",))


def test_verified_public_source_is_not_cross_user_personal_memory(store):
    source(store)
    item_id = remember(store)
    rows = visible(store)
    assert [r["id"] for r in rows] == [item_id]
    assert rows[0]["trust"] == "single"
    assert rows[0]["usage_mode"] == "contextual"
    assert not visible(store, scope=Scope(2, 10, 200, True))
    assert not visible(store, channels=())  # No live permission -> no disclosure.
    assert not visible(store, members=())   # No current contributors -> no disclosure.
    assert store.server_memory_candidates(
        Scope(None, 10, 200), readable_channel_ids=[10], member_user_ids=[100]
    ) == []


def test_server_admin_mode_is_separate_from_current_speaker_preference(store):
    source(store)
    remember(store)
    store.set_user_memory_mode(200, "off")
    assert len(visible(store)) == 1
    store.set_memory_mode_override("guild:1", "write_only")
    assert visible(store) == []
    store.set_memory_mode_override("guild:1", None)
    assert len(visible(store)) == 1


@pytest.mark.parametrize("fault", ["implicit", "foreign", "wrong_evidence", "missing"])
def test_untrusted_or_out_of_scope_input_never_creates_an_item(store, fault):
    if fault != "missing":
        source(store, guild=2 if fault == "foreign" else 1, direct=fault != "implicit")
    with pytest.raises(ValueError):
        store.add_server_memory(
            1, content="스스로 만든 주장",
            evidence="없는 인용" if fault == "wrong_evidence" else "매주 모임",
            sources=[ServerSourceRef("99999" if fault == "missing" else "1001", "user")],
        )
    assert store._server_memories.items(1) == []


def test_admin_trust_and_directives_cannot_be_forged_by_extractor(store):
    source(store)
    with pytest.raises(ValueError, match="admins"):
        remember(store, usage_mode="directive")
    item = remember(store, usage_mode="ambient")
    assert store._server_memories.items(1)[0]["id"] == item
    assert store._server_memories.items(1)[0]["usage_mode"] == "contextual"


def test_personal_write_off_and_public_contribution_opt_out(store):
    source(store)
    store.set_user_memory_mode(100, "read_only")
    with pytest.raises(PermissionError):
        remember(store)
    store.set_user_memory_mode(100, "normal")
    item = remember(store)
    assert len(visible(store)) == 1
    assert store.set_server_memory_opt_out(1, 100, True) == 1
    assert store.server_memory_opted_out(1, 100)
    assert visible(store) == []
    assert store._server_memories.sources(item) == []
    with pytest.raises(PermissionError):
        remember(store)
    store.set_server_memory_opt_out(1, 100, False)
    assert not store.server_memory_opted_out(1, 100)
    assert visible(store) == []  # Opt-in does not resurrect deleted data.


def test_private_channel_remains_local_and_cannot_mix_public_and_private(store):
    source(store, public=False, channel=10)
    item = remember(store)
    assert visible(store)[0]["id"] == item
    assert not visible(store, scope=Scope(1, 11, 200, True), channels=(10, 11))
    source(store, message=1002, channel=11)
    with pytest.raises(ValueError, match="disclosure spaces"):
        store.add_server_memory(
            1, content="cross-space", evidence="매주 모임",
            sources=[ServerSourceRef("1001", "user"), ServerSourceRef("1002", "user")],
        )
    assert len(store._server_memories.items(1)) == 1


def test_member_subject_must_speak_for_self_and_be_relevant(store):
    source(store)
    with pytest.raises(ValueError, match="subject"):
        remember(store, subject_user_ids=(999,))
    remember(store, subject_user_ids=(100,))
    assert not visible(store)  # Unrelated member's profile cannot leak.
    assert not visible(store, related=(100,), members=(200,))
    assert len(visible(store, related=(100,))) == 1
    assert not visible(store, scope=Scope(1, 10, 100, True), related=(100,))


def test_two_independent_contributors_upgrade_trust_but_not_claimed_admin(store):
    source(store, user=100, message=1001)
    source(store, user=101, message=1002, content="매주 모임 맞아요")
    item = remember(store, usage_mode="ambient")
    again = remember(store, message=1002, usage_mode="ambient")
    assert item == again
    saved = store._server_memories.items(1)[0]
    assert saved["trust"] == "corroborated"
    assert saved["usage_mode"] == "ambient"
    assert saved["confirmations"] == 2
    assert {x["author_user_id"] for x in store._server_memories.sources(item)} == {"100", "101"}


def test_hina_reply_is_observed_only_for_non_member_community_memory(store):
    source(store)
    item = store.add_server_memory(
        1, content="히나가 다음 주 모임에 인사하기로 함",
        evidence="다음 주에도 만나자",
        sources=[ServerSourceRef("1001", "hina")], usage_mode="ambient",
    )
    assert store._server_memories.items(1)[0]["trust"] == "observed"
    assert store._server_memories.items(1)[0]["id"] == item
    source(store, message=1002, history_lookup=True)
    with pytest.raises(ValueError, match="lookup"):
        store.add_server_memory(
            1, content="재검색 후 생성된 약속", evidence="다음 주에도 만나자",
            sources=[ServerSourceRef("1002", "hina")],
        )


def test_deleting_source_removes_item_and_evidence_atomically(store):
    source(store)
    item = remember(store)
    assert store.forget_server_memory_source(1, 99, 1001) == 0
    assert len(visible(store)) == 1
    assert store.forget_server_memory_source(1, 10, 1001) == 1
    assert store._server_memories.sources(item) == []
    assert visible(store) == []


@pytest.mark.parametrize("action", ["forget", "purge_channel", "purge_realm", "purge_all"])
def test_all_existing_deletion_paths_remove_community_sources(store, action):
    source(store)
    item = remember(store)
    scope = Scope(1, 10, 100, True)
    if action == "forget":
        store.forget(scope)
    elif action == "purge_channel":
        store.purge_channel_memory(scope)
    elif action == "purge_realm":
        store.purge_realm_memory(scope)
    else:
        store.purge_all_memory()
    assert store._server_memories.items(1) == []
    assert store._server_memories.sources(item) == []


def test_live_permission_matrix_does_not_fall_back_to_old_channel_metadata(store):
    source(store, message=1001, channel=10)
    source(store, message=1002, channel=11)
    item = store.add_server_memory(
        1, content="공개 채널 두 곳에서 확인한 공지", evidence="매주 모임",
        sources=[ServerSourceRef("1001", "user"), ServerSourceRef("1002", "user")],
    )
    assert not visible(store, channels=(10,))
    assert visible(store, channels=(10, 11))[0]["id"] == item
    assert store._server_memories.purge_channel(1, 11) == 1
    assert visible(store, channels=(10, 11)) == []
