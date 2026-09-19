import sqlite3

import pytest

from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind, RelationshipEvidence
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_store_migrates_existing_memory_items_with_empty_relationship_evidence(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            kind TEXT NOT NULL,
            origin_realm TEXT NOT NULL,
            origin_channel_id TEXT NOT NULL,
            origin_public_at_capture INTEGER NOT NULL,
            disclosure TEXT NOT NULL,
            source_message_ids TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO memory_items(
            user_id,content,kind,origin_realm,origin_channel_id,
            origin_public_at_capture,disclosure,source_message_ids,confidence
        ) VALUES (
            '100','기존 관계 기억','relationship','dm:100','10',
            0,'implicit','["101"]',0.9
        );
        """
    )
    db.commit()
    db.close()

    store = Store(str(path))

    columns = {
        row["name"] for row in store.db.execute("PRAGMA table_info(memory_items)")
    }
    assert "relationship_evidence" in columns
    item = store.memory_items(100)[0]
    assert item.content == "기존 관계 기억"
    assert not item.relationship_evidence
    store.close()


def test_store_round_trips_relationship_evidence():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)

    store.add_memory_item(
        scope,
        "사용자는 히나와 편한 대화를 반복해 왔다.",
        kind=MemoryKind.RELATIONSHIP,
        disclosure=MemoryDisclosure.IMPLICIT,
        relationship_evidence={
            "familiarity": 3,
            "comfort": 2,
            "task_orientation": 1,
        },
    )

    item = store.memory_items(100)[0]
    assert item.relationship_evidence == RelationshipEvidence(
        familiarity=3,
        comfort=2,
        task_orientation=1,
    )
    store.close()


def test_store_rejects_relationship_evidence_on_non_relationship_memory():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)

    with pytest.raises(ValueError):
        store.add_memory_item(
            scope,
            "사용자는 차를 좋아한다.",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
            relationship_evidence={"familiarity": 2},
        )
    store.close()


@pytest.mark.parametrize(
    "evidence",
    [
        {"familiarity": -1},
        {"familiarity": 5},
        {"familiarity": 2.5},
        {"familiarity": True},
        {"unknown_axis": 2},
    ],
)
def test_relationship_evidence_rejects_invalid_values(evidence):
    with pytest.raises(ValueError):
        RelationshipEvidence.from_mapping(evidence)
