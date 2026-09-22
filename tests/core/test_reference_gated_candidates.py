from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_reference_gated_candidate_query_is_owner_active_and_cross_space_only():
    store = Store(":memory:")
    owner_dm = Scope(None, 10, 100)
    current = Scope(1, 20, 100, True)
    same_guild_public = Scope(1, 30, 100, True)
    same_guild_private = Scope(1, 40, 100, False)
    other_user_dm = Scope(None, 50, 200)
    try:
        cross_id = store.add_memory_item(
            owner_dm,
            "cross-space factual memory",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        private_id = store.add_memory_item(
            same_guild_private,
            "private-channel gated memory",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        store.add_memory_item(
            same_guild_public,
            "same guild public memory",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        store.add_memory_item(
            owner_dm,
            "relationship memory",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
            relationship_evidence={"familiarity": 2},
        )
        store.add_memory_item(
            owner_dm,
            "not gated",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        store.add_memory_item(
            other_user_dm,
            "other user memory",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )

        candidates = store.reference_gated_memory_candidates(current, limit=24)

        assert {item.id for item in candidates} == {cross_id, private_id}
        assert all(item.user_id == "100" for item in candidates)
    finally:
        store.close()


def test_reference_gated_candidate_query_excludes_superseded_items():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    current = Scope(1, 20, 100, True)
    try:
        old_id = store.add_memory_item(
            dm,
            "old interview date",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        new_id = store.add_memory_item(
            dm,
            "new interview date",
            kind=MemoryKind.EVENT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        proposal_id = store.add_memory_reconciliation_proposal(
            dm,
            new_memory_item_id=new_id,
            target_memory_item_id=old_id,
            relation="corrects",
            confidence=0.99,
        )
        assert proposal_id is not None
        assert store.apply_memory_reconciliation_proposal(proposal_id) == "applied"

        candidates = store.reference_gated_memory_candidates(current, limit=24)

        assert [item.id for item in candidates] == [new_id]
    finally:
        store.close()


def test_reference_gated_candidate_query_is_bounded_and_newest_first():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    current = Scope(1, 20, 100, True)
    try:
        ids = [
            store.add_memory_item(
                dm,
                f"memory {index}",
                kind=MemoryKind.FACT,
                disclosure=MemoryDisclosure.REFERENCE_GATED,
            )
            for index in range(5)
        ]

        candidates = store.reference_gated_memory_candidates(current, limit=3)

        assert [item.id for item in candidates] == list(reversed(ids[-3:]))
    finally:
        store.close()
