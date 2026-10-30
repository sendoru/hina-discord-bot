"""Every memory deletion path removes the item's annotation too (#449 §4.6)."""

from dataclasses import replace

import pytest

from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

TARGET = Scope(1, 10, 100, True)
SAME_REALM_OTHER_CHANNEL = Scope(1, 11, 100, True)
OTHER_REALM = Scope(2, 20, 100, True)


def _annotated(store, scope, content):
    item_id = store.add_memory_item(scope, content, kind=MemoryKind.FACT,
                                    disclosure=MemoryDisclosure.LOCAL, source_message_ids=("1",),
                                    confidence=0.9)
    annotation = parse_annotation({}, MemoryKind.FACT, [{"user": content, "at": "2026-10-30 03:00:00"}])
    store.upsert_memory_annotation(replace(annotation, memory_item_id=item_id))
    return item_id


@pytest.mark.parametrize(("operation", "survivors"), [
    ("forget", {"other_realm"}),
    ("purge_channel", {"other_channel", "other_realm"}),
    ("purge_realm", {"other_realm"}),
    ("purge_all", set()),
    ("delete_item", {"other_channel", "other_realm"}),
])
def test_deletion_paths_remove_annotations_with_their_items(operation, survivors):
    store = Store.create(":memory:")
    ids = {
        "target": _annotated(store, TARGET, "target"),
        "other_channel": _annotated(store, SAME_REALM_OTHER_CHANNEL, "other channel"),
        "other_realm": _annotated(store, OTHER_REALM, "other realm"),
    }
    if operation == "forget":
        store.forget(TARGET)
    elif operation == "purge_channel":
        store.purge_channel_memory(TARGET)
    elif operation == "purge_realm":
        store.purge_realm_memory(TARGET)
    elif operation == "purge_all":
        store.purge_all_memory()
    else:
        store.delete_memory_item(ids["target"], expected_revision=0)

    remaining = set(store.memory_annotations(ids.values()))
    assert remaining == {ids[name] for name in survivors}
    assert {row[0] for row in store.db.execute("SELECT memory_item_id FROM memory_item_annotations")} \
        <= {row[0] for row in store.db.execute("SELECT id FROM memory_items")}
    store.close()
