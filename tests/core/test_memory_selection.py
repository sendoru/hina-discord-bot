"""Tiered selection details not covered by the shared fixtures (#457)."""

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from hina_bot.core.memory_annotations import (
    Annotator,
    MemoryAnnotation,
    Stability,
    TimeScope,
    UsageMode,
)
from hina_bot.core.memory_items import MemoryDisclosure, MemoryItem, MemoryKind
from hina_bot.core.memory_selection import (
    EffectiveStatus,
    SelectionContext,
    effective_annotation,
    effective_status,
    is_light_chat,
)
from hina_bot.core.routing import Scope

NOW = datetime(2026, 10, 30, 19, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # Friday
DM = Scope(None, 9, 1)


def _annotation(**fields):
    base = {
        "memory_item_id": 1, "item_revision": 0, "time_scope": TimeScope.ONGOING,
        "stability": Stability.LONG, "observed_at": "2026-10-20 03:00:00",
        "last_confirmed_at": "2026-10-20 03:00:00", "usage_mode": UsageMode.AMBIENT,
        "annotator": Annotator.EXTRACTOR, "annotator_version": "test",
    }
    base.update(fields)
    return MemoryAnnotation(**base)


def _item(item_id=1, content="고양이 보리를 키움", **fields):
    base = {
        "id": item_id, "user_id": "1", "content": content, "kind": MemoryKind.FACT,
        "origin_realm": "dm:1", "origin_channel_id": "9", "origin_public_at_capture": False,
        "disclosure": MemoryDisclosure.LOCAL, "source_message_ids": (), "confidence": 0.9,
        "created_at": "2026-10-20 03:00:00", "updated_at": "2026-10-20 03:00:00",
    }
    base.update(fields)
    return MemoryItem(**base)


@pytest.mark.parametrize(("fields", "status", "day"), [
    ({"time_scope": TimeScope.PLANNED, "stability": None, "event_at": "2026-11"},
     EffectiveStatus.UPCOMING, "2026-11"),
    ({"time_scope": TimeScope.PLANNED, "stability": None, "event_at": "2026-10"},
     EffectiveStatus.UPCOMING, "2026-10"),  # the month is not over yet
    ({"time_scope": TimeScope.PLANNED, "stability": None, "event_at": "2026-09"},
     EffectiveStatus.PAST, "2026-09"),
    ({"time_scope": TimeScope.EPISODIC, "stability": None, "event_at": "2026-10-25"},
     EffectiveStatus.RECENT_EVENT, "2026-10-25"),
    ({"time_scope": TimeScope.EPISODIC, "stability": None, "event_at": "2026-09-01"},
     EffectiveStatus.PAST_EVENT, "2026-09-01"),
    ({"time_scope": TimeScope.RECURRING, "stability": None, "recurrence": "weekly:SAT,SUN"},
     EffectiveStatus.UPCOMING, "2026-10-31"),
    ({"time_scope": TimeScope.RECURRING, "stability": None, "recurrence": "weekly:FRI 19:00"},
     EffectiveStatus.UPCOMING, "2026-10-30"),
    ({"time_scope": TimeScope.RECURRING, "stability": None, "recurrence": "monthly:31"},
     EffectiveStatus.UPCOMING, "2026-10-31"),
    ({"time_scope": TimeScope.RECURRING, "stability": None}, EffectiveStatus.CURRENT, None),
])
def test_effective_status_dates(fields, status, day):
    view = effective_status(_annotation(**fields), NOW)

    assert (view.status, view.date) == (status, day)


def test_undated_plan_expires_after_thirty_days_and_ongoing_goes_unverified():
    stale_plan = _annotation(time_scope=TimeScope.PLANNED, stability=None,
                             observed_at="2026-09-01 00:00:00")
    short = _annotation(stability=Stability.SHORT, last_confirmed_at="2026-09-01 00:00:00")

    assert effective_status(stale_plan, NOW).expired
    assert effective_status(short, NOW).status == EffectiveStatus.UNVERIFIED
    assert effective_status(_annotation(stability=Stability.PERMANENT,
                                        last_confirmed_at="2020-01-01 00:00:00"),
                            NOW).status == EffectiveStatus.CURRENT


def test_missing_and_stale_annotations_use_principle_six_defaults():
    fact, state = effective_annotation(_item(), None)
    boundary, _ = effective_annotation(_item(kind=MemoryKind.BOUNDARY), None)
    edited = _item(revision=2)
    stale_fact, stale_state = effective_annotation(edited, _annotation(keywords=("보리",)))

    assert state == "missing" and fact.usage_mode == UsageMode.CONTEXTUAL
    assert boundary.usage_mode == UsageMode.DIRECTIVE
    assert stale_state == "stale" and stale_fact.usage_mode == UsageMode.CONTEXTUAL
    assert stale_fact.keywords == ("보리",)


@pytest.mark.parametrize(("message", "light"), [
    ("히나야 안녕", True), ("ㅎㅇ", True), ("히나야", True), ("오늘 퇴근했다 ㅠㅠ", True),
    ("이 코드 좀 봐줘: print(1/0)", False), ("파이썬 에러 났는데 어떻게 고쳐?", False),
    ("https://example.com 이거 봐", False), ("번역해줘", False),
])
def test_light_chat_detection(message, light):
    assert is_light_chat(message) is light


def test_only_eligible_duplicates_compete_and_local_record_wins():
    from hina_bot.core.memory_selection import full_memory_items

    server = Scope(1, 20, 1, True)
    local = _item(1, "보리   키움", origin_realm=server.realm, origin_channel_id="20")
    remote = _item(2, "보리 키움", disclosure=MemoryDisclosure.GLOBAL)
    sensitive = _item(3, "보리 키움", origin_realm=server.realm, origin_channel_id="20")
    foreign = replace(local, id=4, user_id="2")
    annotations = {3: _annotation(usage_mode=UsageMode.SENSITIVE)}
    ctx = SelectionContext(server, "보리", NOW)
    assert full_memory_items([remote, foreign, sensitive, local], annotations, ctx) == [local]
    assert full_memory_items([sensitive, remote], annotations, ctx) == [remote]
    assert full_memory_items([sensitive], annotations, replace(ctx, scope=DM, message="")) == [sensitive]
