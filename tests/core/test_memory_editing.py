import pytest

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_revise_memory_item_preserves_identity_and_records_history():
    store = Store(":memory:")
    scope = Scope(1, 10, 100, True)
    item_id = store.add_memory_item(
        scope,
        "old relationship",
        kind="relationship",
        disclosure="implicit",
        source_message_ids=("55",),
        confidence=0.7,
        relationship_evidence={"familiarity": 2},
        user_name="User",
    )
    before = store.memory_item(item_id)
    assert before is not None

    revised = store.revise_memory_item(
        item_id,
        expected_revision=0,
        content="corrected relationship",
        kind="relationship",
        disclosure="global",
        confidence=0.9,
        relationship_evidence={"familiarity": 3, "comfort": 1},
        admin_command_id=42,
    )

    assert revised.id == before.id
    assert revised.user_id == before.user_id
    assert revised.user_name == before.user_name
    assert revised.origin_realm == before.origin_realm
    assert revised.origin_channel_id == before.origin_channel_id
    assert revised.origin_public_at_capture == before.origin_public_at_capture
    assert revised.source_message_ids == before.source_message_ids
    assert revised.created_at == before.created_at
    assert revised.status == before.status
    assert revised.superseded_by == before.superseded_by
    assert revised.revision == 1
    assert revised.content == "corrected relationship"
    assert revised.kind.value == "relationship"
    assert revised.disclosure.value == "global"
    assert revised.confidence == 0.9
    assert revised.relationship_evidence.as_dict() == {"familiarity": 3, "comfort": 1}

    history = store.memory_item_edit_history(item_id)
    assert len(history) == 1
    snapshot = history[0]
    assert snapshot["revision"] == 0
    assert snapshot["content"] == "old relationship"
    assert snapshot["kind"] == "relationship"
    assert snapshot["disclosure"] == "implicit"
    assert snapshot["confidence"] == 0.7
    assert snapshot["relationship_evidence"] == '{"familiarity":2}'
    assert snapshot["admin_command_id"] == 42

    with pytest.raises(ValueError, match="changed"):
        store.revise_memory_item(
            item_id,
            expected_revision=0,
            content="stale edit",
            kind="relationship",
            disclosure="global",
            confidence=0.9,
            relationship_evidence={"familiarity": 4},
        )

    unchanged = store.revise_memory_item(
        item_id,
        expected_revision=1,
        content=" corrected relationship ",
        kind="relationship",
        disclosure="global",
        confidence=0.9,
        relationship_evidence={"comfort": 1, "familiarity": 3},
    )
    assert unchanged.revision == 1
    assert len(store.memory_item_edit_history(item_id)) == 1
    store.close()


def test_memory_edit_validation_matches_store_invariants():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    with pytest.raises(ValueError, match="at most 600"):
        store.add_memory_item(
            scope,
            "x" * 601,
            kind="fact",
            disclosure="local",
        )

    item_id = store.add_memory_item(
        scope,
        "fact",
        kind="fact",
        disclosure="local",
    )
    with pytest.raises(ValueError, match="relationship_evidence"):
        store.revise_memory_item(
            item_id,
            expected_revision=0,
            content="fact",
            kind="fact",
            disclosure="local",
            confidence=1,
            relationship_evidence={"comfort": 1},
        )
    with pytest.raises(ValueError, match="comfort"):
        store.revise_memory_item(
            item_id,
            expected_revision=0,
            content="relationship",
            kind="relationship",
            disclosure="implicit",
            confidence=1,
            relationship_evidence={"comfort": 5},
        )
    with pytest.raises(ValueError, match="confidence"):
        store.revise_memory_item(
            item_id,
            expected_revision=0,
            content="fact",
            kind="fact",
            disclosure="local",
            confidence=float("nan"),
        )
    store.close()


def test_memory_purge_removes_edit_history_snapshots():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    item_id = store.add_memory_item(
        scope,
        "before",
        kind="fact",
        disclosure="local",
    )
    store.revise_memory_item(
        item_id,
        expected_revision=0,
        content="after",
        kind="fact",
        disclosure="local",
        confidence=1,
    )
    assert len(store.memory_item_edit_history(item_id)) == 1

    store.purge_channel_memory(scope)

    assert store.memory_item(item_id) is None
    assert store.memory_item_edit_history(item_id) == []
    store.close()
