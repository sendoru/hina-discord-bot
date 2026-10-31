"""Tiered selection details not covered by the shared fixtures (#457)."""

import json
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
    render_user_memory,
    select_personal_memory,
    selection_provenance,
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


def test_rendered_block_and_provenance_hold_no_ids_kinds_or_scores():
    items = [_item(1), _item(2, "업무 얘기는 먼저 꺼내지 말아 달라고 함", kind=MemoryKind.BOUNDARY)]
    annotations = {1: _annotation(keywords=("보리",))}
    ctx = SelectionContext(scope=DM, message="보리 오늘 귀여웠어", now=NOW)

    selection = select_personal_memory(items, annotations, ctx)
    rendered = render_user_memory(selection, DM)
    provenance = selection_provenance(selection)

    assert rendered == {
        "directives": ["업무 얘기는 먼저 꺼내지 말아 달라고 함"],
        "quiet_boundaries": [],
        "notes": [{"text": "고양이 보리를 키움", "status": "current", "noted": "2026-10-20",
                   "use": "free"}],
    }
    assert provenance["tiers"] == {"T0": 1, "T2": 1}
    assert {entry["store"] for entry in provenance["items"]} == {"personal"}
    assert "보리" not in json.dumps(provenance, ensure_ascii=False)


def test_zero_injection_is_normal_and_other_owners_never_leak():
    other = _item(3, "다른 사람의 고양이", user_id="2")
    ctx = SelectionContext(scope=DM, message="고양이 얘기 하자", now=NOW)

    selection = select_personal_memory(
        [_item(1, "회사에서 결제 시스템을 담당함"), other],
        {1: _annotation(usage_mode=UsageMode.CONTEXTUAL, keywords=("회사",)),
         3: replace(_annotation(keywords=("고양이",)), memory_item_id=3)},
        ctx,
    )

    assert selection.selected == []
