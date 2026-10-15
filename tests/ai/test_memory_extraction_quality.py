"""Regression tests for durable memory admission and relationship reconciliation."""

from dataclasses import replace

from hina_bot.ai.memory_extraction import (
    ExtractedMemoryItem,
    persist_shadow_items_detailed,
    select_reconciliation_candidates,
)
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind, RelationshipEvidence
from hina_bot.core.relationship_profile import aggregate_owner_relationship_evidence
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def _item(content, source, *, kind=MemoryKind.FACT, evidence=None):
    return ExtractedMemoryItem(
        content=content,
        kind=kind,
        disclosure=(
            MemoryDisclosure.IMPLICIT
            if kind == MemoryKind.RELATIONSHIP else MemoryDisclosure.LOCAL
        ),
        confidence=0.99,
        source_message_ids=(source,),
        relationship_evidence=evidence or RelationshipEvidence(),
    )


def test_same_content_from_later_batch_is_not_stored_again():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    try:
        first = persist_shadow_items_detailed(
            store, scope, (_item("사용자는 커피를 좋아한다.", "101"),)
        )
        second = persist_shadow_items_detailed(
            store, scope, (_item("사용자는 커피를 좋아한다.", "201"),)
        )
        assert (first.stored, first.duplicates) == (1, 0)
        assert (second.stored, second.duplicates, second.item_ids) == (0, 1, (None,))
        assert len(store.memory_items(100)) == 1
    finally:
        store.close()


def test_identical_content_does_not_cross_source_channel_or_privacy():
    store = Store(":memory:")
    private = Scope(1, 10, 100, False)
    public = Scope(1, 20, 100, True)
    try:
        item = _item("사용자는 커피를 좋아한다.", "101")
        assert persist_shadow_items_detailed(store, private, (item,)).stored == 1
        assert persist_shadow_items_detailed(
            store, public, (replace(item, source_message_ids=("201",)),)
        ).stored == 1
        assert len(store.memory_items(100)) == 2
    finally:
        store.close()


def test_relationship_observations_only_dedupe_equal_evidence():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    content = "사용자는 히나와 편안하게 대화한다."
    first = _item(
        content, "101", kind=MemoryKind.RELATIONSHIP,
        evidence=RelationshipEvidence(comfort=2),
    )
    same = replace(first, source_message_ids=("201",))
    stronger = replace(
        first,
        source_message_ids=("301",),
        relationship_evidence=RelationshipEvidence(comfort=3),
    )
    try:
        assert persist_shadow_items_detailed(store, scope, (first,)).stored == 1
        assert persist_shadow_items_detailed(store, scope, (same,)).duplicates == 1
        assert persist_shadow_items_detailed(store, scope, (stronger,)).stored == 1
        assert len(store.memory_items(100)) == 2
    finally:
        store.close()


def test_reconciliation_pool_keeps_older_facts_after_many_relationships():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    try:
        first = store.add_memory_item(
            scope, "사용자는 Rust를 좋아한다.",
            kind=MemoryKind.PREFERENCE, disclosure=MemoryDisclosure.LOCAL,
        )
        for index in range(140):
            store.add_memory_item(
                scope, f"관계 관찰 {index}",
                kind=MemoryKind.RELATIONSHIP,
                disclosure=MemoryDisclosure.IMPLICIT,
            )
        pool = store.memory_reconciliation_candidates(scope, limit=96)
        selected = select_reconciliation_candidates(
            pool, [{"user": "Rust가 좋아"}], limit=24
        )
        assert first in {item.id for item in pool}
        assert first in {item.id for item in selected}
        assert len(selected) <= 24
    finally:
        store.close()


def test_relationship_duplicate_is_applied_without_changing_owner_profile():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    evidence = RelationshipEvidence(familiarity=2, comfort=3)
    try:
        old = store.add_memory_item(
            scope, "사용자는 히나와 편하게 대화한다.",
            kind=MemoryKind.RELATIONSHIP, disclosure=MemoryDisclosure.IMPLICIT,
            relationship_evidence=evidence, source_message_ids=("101",),
        )
        before = aggregate_owner_relationship_evidence(store.memory_items(100), scope)
        new = store.add_memory_item(
            scope, "사용자와 히나는 부담 없이 대화하는 관계이다.",
            kind=MemoryKind.RELATIONSHIP, disclosure=MemoryDisclosure.IMPLICIT,
            relationship_evidence=evidence, source_message_ids=("201",),
        )
        proposal = store.add_memory_reconciliation_proposal(
            scope, new_memory_item_id=new, target_memory_item_id=old,
            relation="duplicate", confidence=0.99, source_message_ids=("201",),
        )
        assert store.apply_memory_reconciliation_proposal(proposal) == "applied"
        assert store.apply_memory_reconciliation_proposal(proposal) == "already_applied"
        after = aggregate_owner_relationship_evidence(store.memory_items(100), scope)
        assert before == after
        assert [item.id for item in store.memory_items(100)] == [old]
        assert store.memory_item(new).superseded_by == old
    finally:
        store.close()


def test_relationship_different_evidence_or_scope_remains_unmerged():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    server = Scope(1, 20, 100, True)
    try:
        old = store.add_memory_item(
            dm, "사용자는 편하게 이야기한다.",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            relationship_evidence=RelationshipEvidence(comfort=2),
        )
        stronger = store.add_memory_item(
            dm, "사용자는 더 편하게 이야기한다.",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            relationship_evidence=RelationshipEvidence(comfort=3),
        )
        proposal = store.add_memory_reconciliation_proposal(
            dm, new_memory_item_id=stronger, target_memory_item_id=old,
            relation="duplicate", confidence=0.99,
        )
        assert store.apply_memory_reconciliation_proposal(proposal) == "relationship_deferred"
        server_item = store.add_memory_item(
            server, "사용자는 편하게 이야기한다.",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            relationship_evidence=RelationshipEvidence(comfort=2),
        )
        other = store.add_memory_reconciliation_proposal(
            dm, new_memory_item_id=stronger, target_memory_item_id=server_item,
            relation="duplicate", confidence=0.99,
        )
        assert store.apply_memory_reconciliation_proposal(other) == "relationship_deferred"
    finally:
        store.close()
