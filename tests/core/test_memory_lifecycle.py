import sqlite3

from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind, MemoryStatus
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def _pair(
    store: Store,
    scope: Scope,
    *,
    relation: str,
    confidence: float = 0.99,
    kind: MemoryKind = MemoryKind.FACT,
):
    target_id = store.add_memory_item(
        scope,
        "old memory",
        kind=kind,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("1",),
    )
    new_id = store.add_memory_item(
        scope,
        "new memory",
        kind=kind,
        disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("2",),
    )
    proposal_id = store.add_memory_reconciliation_proposal(
        scope,
        new_memory_item_id=new_id,
        target_memory_item_id=target_id,
        relation=relation,
        confidence=confidence,
        source_message_ids=("2",),
    )
    assert proposal_id is not None
    return target_id, new_id, proposal_id


def test_high_confidence_correction_supersedes_target():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        target_id, new_id, proposal_id = _pair(
            store,
            scope,
            relation="corrects",
        )

        outcome = store.apply_memory_reconciliation_proposal(proposal_id)

        assert outcome == "applied"
        assert [item.id for item in store.memory_items(100)] == [new_id]
        history = store.memory_items(100, include_superseded=True)
        target = next(item for item in history if item.id == target_id)
        new = next(item for item in history if item.id == new_id)
        assert target.status == MemoryStatus.SUPERSEDED
        assert target.superseded_by == new_id
        assert new.status == MemoryStatus.ACTIVE
    finally:
        store.close()


def test_high_confidence_duplicate_supersedes_new_item():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        target_id, new_id, proposal_id = _pair(
            store,
            scope,
            relation="duplicate",
        )

        outcome = store.apply_memory_reconciliation_proposal(proposal_id)

        assert outcome == "applied"
        assert [item.id for item in store.memory_items(100)] == [target_id]
        history = store.memory_items(100, include_superseded=True)
        new = next(item for item in history if item.id == new_id)
        assert new.status == MemoryStatus.SUPERSEDED
        assert new.superseded_by == target_id
    finally:
        store.close()


def test_low_confidence_and_conflict_proposals_remain_active():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        low_target, low_new, low_proposal = _pair(
            store,
            scope,
            relation="corrects",
            confidence=0.94,
        )
        conflict_target, conflict_new, conflict_proposal = _pair(
            store,
            scope,
            relation="conflicts",
        )

        assert store.apply_memory_reconciliation_proposal(low_proposal) == "below_threshold"
        assert store.apply_memory_reconciliation_proposal(conflict_proposal) == "conflict_deferred"
        assert {item.id for item in store.memory_items(100)} == {
            low_target,
            low_new,
            conflict_target,
            conflict_new,
        }
    finally:
        store.close()


def test_relationship_reconciliation_is_not_auto_applied():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        target_id, new_id, proposal_id = _pair(
            store,
            scope,
            relation="corrects",
            kind=MemoryKind.RELATIONSHIP,
        )

        outcome = store.apply_memory_reconciliation_proposal(proposal_id)

        assert outcome == "relationship_deferred"
        assert {item.id for item in store.memory_items(100)} == {target_id, new_id}
    finally:
        store.close()


def test_superseded_items_are_not_reconciliation_candidates():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        target_id, new_id, proposal_id = _pair(
            store,
            scope,
            relation="corrects",
        )
        assert store.apply_memory_reconciliation_proposal(proposal_id) == "applied"

        candidates = store.memory_reconciliation_candidates(scope)

        assert [item.id for item in candidates] == [new_id]
        assert target_id not in {item.id for item in candidates}
    finally:
        store.close()


def test_reconciliation_application_is_idempotent():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        _, _, proposal_id = _pair(store, scope, relation="corrects")
        assert store.apply_memory_reconciliation_proposal(proposal_id) == "applied"
        assert store.apply_memory_reconciliation_proposal(proposal_id) == "already_applied"
    finally:
        store.close()


def test_retracted_item_is_excluded_from_runtime_and_retry_history():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        item_id = store.add_memory_item(
            scope,
            "memory to retract",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
            source_message_ids=("1",),
        )

        retracted = store.retract_memory_item(item_id, expected_revision=0)

        assert retracted.status == MemoryStatus.RETRACTED
        assert retracted.revision == 1
        assert retracted.superseded_by is None
        assert store.memory_items(100) == []
        assert store.memory_items(100, include_superseded=True) == []
        assert store.memory_reconciliation_candidates(scope) == []
        stored = store.memory_item(item_id)
        assert stored is not None
        assert stored.status == MemoryStatus.RETRACTED
    finally:
        store.close()


def test_retraction_rejects_stale_or_non_active_item():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        item_id = store.add_memory_item(
            scope,
            "active memory",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        store.revise_memory_item(
            item_id,
            expected_revision=0,
            content="edited memory",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
            confidence=1,
        )

        try:
            store.retract_memory_item(item_id, expected_revision=0)
        except ValueError as exc:
            assert "changed" in str(exc)
        else:
            raise AssertionError("stale retract should fail")

        retracted = store.retract_memory_item(item_id, expected_revision=1)
        assert retracted.status == MemoryStatus.RETRACTED

        try:
            store.retract_memory_item(item_id, expected_revision=2)
        except ValueError as exc:
            assert "active" in str(exc)
        else:
            raise AssertionError("retracting a non-active item should fail")
    finally:
        store.close()


def test_delete_memory_item_removes_owned_records_and_keeps_other_lifecycle_state():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        old_id, replacement_id, proposal_id = _pair(
            store, scope, relation="corrects"
        )
        replacement = store.revise_memory_item(
            replacement_id,
            expected_revision=0,
            content="corrected memory",
            kind="fact",
            disclosure="local",
            confidence=1,
        )
        assert replacement.revision == 1
        assert store.apply_memory_reconciliation_proposal(proposal_id) == "applied"
        assert store.memory_item(old_id).superseded_by == replacement_id

        store.delete_memory_item(replacement_id, expected_revision=1)

        assert store.memory_item(replacement_id) is None
        assert store.memory_item_edit_history(replacement_id) == []
        assert store.memory_reconciliation_proposals(100) == []
        old = store.memory_item(old_id)
        assert old.status == MemoryStatus.SUPERSEDED
        assert old.superseded_by is None
        assert old.revision == 1
        # A superseded item must never be implicitly reactivated.
        assert store.memory_items(100) == []

        retracted_id = store.add_memory_item(
            scope, "retracted memory", kind="fact", disclosure="local"
        )
        store.retract_memory_item(retracted_id, expected_revision=0)
        store.delete_memory_item(retracted_id, expected_revision=1)
        store.delete_memory_item(old_id, expected_revision=1)
        assert store.memory_item(retracted_id) is None
        assert store.memory_item(old_id) is None

        # AUTOINCREMENT does not recycle a deleted memory identifier.
        new_id = store.add_memory_item(
            scope, "unrelated memory", kind="fact", disclosure="local"
        )
        assert new_id > retracted_id
    finally:
        store.close()


def test_delete_memory_item_checks_revision_and_rolls_back_on_failure():
    store = Store.create(":memory:")
    scope = Scope(None, 10, 100)
    try:
        item_id = store.add_memory_item(
            scope, "original", kind="fact", disclosure="local"
        )
        store.revise_memory_item(
            item_id,
            expected_revision=0,
            content="edited",
            kind="fact",
            disclosure="local",
            confidence=1,
        )
        try:
            store.delete_memory_item(item_id, expected_revision=0)
        except ValueError as exc:
            assert "changed" in str(exc)
        else:
            raise AssertionError("stale deletion must fail")

        store.db.execute(
            """CREATE TRIGGER block_deletion BEFORE DELETE ON memory_items
               BEGIN SELECT RAISE(ABORT, 'blocked'); END"""
        )
        try:
            store.delete_memory_item(item_id, expected_revision=1)
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("failed deletion must roll back")
        assert store.memory_item(item_id) is not None
        assert len(store.memory_item_edit_history(item_id)) == 1
        store.db.execute("DROP TRIGGER block_deletion")
        store.delete_memory_item(item_id, expected_revision=1)
        assert store.memory_item(item_id) is None
        assert store.memory_item_edit_history(item_id) == []
    finally:
        store.close()
