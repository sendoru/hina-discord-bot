"""Reply usage bookkeeping: cooldown and follow-up attempts (#449 §7 5, §7.1, #457)."""

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo


from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.core.memory_annotations import Annotator, UsageMode
from hina_bot.core.memory_context import CURRENT_MEMORY_SELECTION
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.memory_selection import (
    MemoryReadRun,
    MemoryUsage,
    SelectionContext,
    memory_usage,
    decision_memory_candidates,
    MemorySelection,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.bot import HinaClient

NOW = datetime(2026, 10, 30, 19, 0, tzinfo=ZoneInfo("Asia/Seoul"))
DM = Scope(None, 10, 100)


def _store():
    store = Store.create(":memory:")
    food = store.add_memory_item(DM, "매운 음식을 좋아함", kind=MemoryKind.PREFERENCE,
                                 disclosure=MemoryDisclosure.LOCAL)
    exam = store.add_memory_item(DM, "자격증 필기시험", kind=MemoryKind.EVENT,
                                 disclosure=MemoryDisclosure.LOCAL)
    cited = [{"user": "매운 음식 좋아", "at": "2026-10-20 03:00:00"}]
    store.upsert_memory_annotation(replace(parse_annotation(
        {"evidence": "매운 음식 좋아", "time_scope": "ongoing", "stability": "long",
         "usage_mode": "ambient", "keywords": ["음식", "매운", "배고픔"]},
        MemoryKind.PREFERENCE, cited), memory_item_id=food))
    store.upsert_memory_annotation(replace(parse_annotation(
        {"time_scope": "planned", "event_at": "2026-10-25", "usage_mode": "ambient",
         "importance": 3, "keywords": ["시험", "자격증"]},
        MemoryKind.EVENT, cited), memory_item_id=exam))
    return store, food, exam


def _select(store, message, *, now=NOW, hours=1, **mode):
    items = store.memory_items(100)
    ctx = SelectionContext(scope=DM, message=message, now=now, hours_since_last_turn=hours)
    return MemorySelection(selected=decision_memory_candidates(items, store.memory_annotations([i.id for i in items]), ctx))


def test_unused_note_is_not_marked():
    store, food, _exam = _store()
    selection = _select(store, "아 배고프다")

    assert memory_usage(selection, "그래? 뭐라도 먹어", NOW) == []
    assert store.memory_annotation(food).last_used_at is None
    store.close()


def test_usage_never_changes_judgments_and_skips_unannotated_items():
    store, food, _exam = _store()
    admin = replace(store.memory_annotation(food), annotator=Annotator.ADMIN,
                    usage_mode=UsageMode.CONTEXTUAL)
    store.upsert_memory_annotation(admin)
    bare = store.add_memory_item(DM, "고양이를 키움", kind=MemoryKind.FACT,
                                 disclosure=MemoryDisclosure.LOCAL)
    updated = store.record_memory_usage([
        MemoryUsage(food, True, last_used_at="2026-10-30 10:00:00"),
        MemoryUsage(bare, True, last_used_at="2026-10-30 10:00:00"),
    ])

    after = store.memory_annotation(food)
    assert updated == 1
    assert after.last_used_at == "2026-10-30 10:00:00"
    assert (after.annotator, after.usage_mode) == (Annotator.ADMIN, UsageMode.CONTEXTUAL)
    assert store.memory_annotation(bare) is None
    store.close()


def test_bot_records_usage_once_and_isolates_failures():
    store, food, _exam = _store()
    events = []
    client = NS(store=store, events=NS(emit=lambda event, **fields: events.append((event, fields))))
    CURRENT_MEMORY_SELECTION.set(MemoryReadRun("decision", _select(store, "아 배고프다")))

    HinaClient._record_memory_usage(client, "매운 떡볶이 어때?", "dm")
    HinaClient._record_memory_usage(client, "매운 떡볶이 어때?", "dm")

    assert store.memory_annotation(food).last_used_at is not None
    assert [event for event, _ in events] == ["memory.usage"]
    assert events[0][1]["memory_used_items"] == 1

    broken = NS(store=NS(record_memory_usage=lambda usages: 1 / 0),
                events=client.events)
    CURRENT_MEMORY_SELECTION.set(MemoryReadRun(
        "decision", _select(store, "아 배고프다", now=NOW + timedelta(days=4)),
    ))
    HinaClient._record_memory_usage(broken, "매운 거", "dm")
    assert events[-1][0] == "memory.usage_failed"
    store.close()


