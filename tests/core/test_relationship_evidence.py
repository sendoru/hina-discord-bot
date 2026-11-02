
import pytest

from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind, RelationshipEvidence
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_store_round_trips_relationship_evidence():
    store = Store.create(":memory:")
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
    store = Store.create(":memory:")
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
