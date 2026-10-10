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


def test_existing_memory_schema_migrates_to_active_lifecycle(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            kind TEXT NOT NULL,
            origin_realm TEXT NOT NULL,
            origin_channel_id TEXT NOT NULL,
            origin_public_at_capture INTEGER NOT NULL,
            disclosure TEXT NOT NULL,
            source_message_ids TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 1.0,
            relationship_evidence TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO memory_items(
            user_id,user_name,content,kind,origin_realm,origin_channel_id,
            origin_public_at_capture,disclosure,source_message_ids,confidence,
            relationship_evidence
        ) VALUES (
            '100','Sendol','legacy memory','fact','dm:100','10',0,'local','["1"]',0.9,'{}'
        );
        """
    )
    db.commit()
    db.close()

    store = Store(str(path))
    try:
        columns = {
            row["name"] for row in store.db.execute("PRAGMA table_info(memory_items)")
        }
        assert {"status", "superseded_by", "user_name"} <= columns
        item = store.memory_items(100)[0]
        assert item.status == MemoryStatus.ACTIVE
        assert item.superseded_by is None
        assert item.user_name == "Sendol"
    finally:
        store.close()


def test_high_confidence_correction_supersedes_target():
    store = Store(":memory:")
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
    store = Store(":memory:")
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
    store = Store(":memory:")
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
    store = Store(":memory:")
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
    store = Store(":memory:")
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
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    try:
        _, _, proposal_id = _pair(store, scope, relation="corrects")
        assert store.apply_memory_reconciliation_proposal(proposal_id) == "applied"
        assert store.apply_memory_reconciliation_proposal(proposal_id) == "already_applied"
    finally:
        store.close()


def test_existing_active_superseded_constraint_migrates_for_retraction(tmp_path):
    path = tmp_path / "pre-retract.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN (
                'fact','event','preference','relationship','boundary','task'
            )),
            origin_realm TEXT NOT NULL,
            origin_channel_id TEXT NOT NULL,
            origin_public_at_capture INTEGER NOT NULL CHECK(origin_public_at_capture IN (0,1)),
            disclosure TEXT NOT NULL CHECK(disclosure IN (
                'local','implicit','reference_gated','global'
            )),
            source_message_ids TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence >= 0 AND confidence <= 1),
            relationship_evidence TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded')),
            superseded_by INTEGER,
            revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO memory_items(
            user_id,user_name,content,kind,origin_realm,origin_channel_id,
            origin_public_at_capture,disclosure,status
        ) VALUES (
            '100','Sendol','pre retract','fact','dm:100','10',0,'local','active'
        );
        UPDATE memory_items SET revision=2,source_message_ids='["55"]',
            confidence=0.75,created_at='2025-01-01 00:00:00',
            updated_at='2025-01-02 00:00:00' WHERE id=1;
        INSERT INTO memory_items(
            user_id,user_name,content,kind,origin_realm,origin_channel_id,
            origin_public_at_capture,disclosure,status,superseded_by
        ) VALUES (
            '100','Sendol','superseded memory','fact','dm:100','10',0,
            'local','superseded',1
        );
        INSERT INTO memory_items(
            user_id,user_name,content,kind,origin_realm,origin_channel_id,
            origin_public_at_capture,disclosure,status
        ) VALUES (
            '100','Sendol','deleted memory','fact','dm:100','10',0,'local','active'
        );
        DELETE FROM memory_items WHERE id=3;
        CREATE TABLE memory_item_edit_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_item_id INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            content TEXT NOT NULL,
            kind TEXT NOT NULL,
            disclosure TEXT NOT NULL,
            confidence REAL NOT NULL,
            relationship_evidence TEXT NOT NULL DEFAULT '{}',
            edited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            admin_command_id INTEGER
        );
        INSERT INTO memory_item_edit_history(
            memory_item_id,revision,content,kind,disclosure,confidence
        ) VALUES (1,1,'previous content','fact','local',0.9);
        CREATE TABLE memory_reconciliation_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            origin_realm TEXT NOT NULL,
            origin_channel_id TEXT NOT NULL,
            new_memory_item_id INTEGER NOT NULL,
            target_memory_item_id INTEGER NOT NULL,
            relation TEXT NOT NULL,
            confidence REAL NOT NULL,
            source_message_ids TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO memory_reconciliation_proposals(
            user_id,origin_realm,origin_channel_id,new_memory_item_id,
            target_memory_item_id,relation,confidence
        ) VALUES ('100','dm:100','10',2,1,'duplicate',0.99);
        """
    )
    assert db.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='memory_items'"
    ).fetchone()[0] == 3
    db.commit()
    db.close()

    store = Store(str(path))
    try:
        item = store.memory_items(100)[0]
        assert item.id == 1
        assert item.revision == 2
        assert item.source_message_ids == ("55",)
        assert item.confidence == 0.75
        assert item.user_name == "Sendol"
        assert item.created_at == "2025-01-01 00:00:00"
        assert item.updated_at == "2025-01-02 00:00:00"
        superseded = store.memory_item(2)
        assert superseded is not None
        assert superseded.status == MemoryStatus.SUPERSEDED
        assert superseded.superseded_by == 1
        assert store.memory_item_edit_history(1)[0]["content"] == "previous content"
        proposals = store.memory_reconciliation_proposals(100)
        assert len(proposals) == 1
        assert (proposals[0]["new_memory_item_id"], proposals[0]["target_memory_item_id"]) == (2, 1)

        new_id = store.add_memory_item(
            Scope(None, 10, 100),
            "new memory after migration",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        assert new_id == 4  # The deleted ID 3 must never be reused.
        retracted = store.retract_memory_item(item.id, expected_revision=2)
        assert retracted.status == MemoryStatus.RETRACTED
        assert retracted.revision == 3
    finally:
        store.close()

    # Opening the migrated DB again must not rebuild or lose prior history.
    reopened = Store(str(path))
    try:
        assert reopened.memory_item(1).status == MemoryStatus.RETRACTED
        assert reopened.memory_item_edit_history(1)[0]["content"] == "previous content"
        assert len(reopened.memory_reconciliation_proposals(100)) == 1
        assert reopened.memory_item(4).content == "new memory after migration"
        assert reopened.db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='memory_items'"
        ).fetchone()[0] == 4
    finally:
        reopened.close()


def test_retracted_item_is_excluded_from_runtime_and_retry_history():
    store = Store(":memory:")
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
    store = Store(":memory:")
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
