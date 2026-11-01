"""Shadow coverage: which notes a legacy answer used that tiered would have missed."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.ai.request_assembly import _visual_turn
from hina_bot.ai.structured_memory_context import recent_own_texts
from hina_bot.ai.vision import VisualInput
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE, CURRENT_MEMORY_SELECTION
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.memory_selection import (
    MemoryReadRun,
    SelectionContext,
    legacy_notes,
    select_personal_memory,
    shadow_coverage,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.bot import HinaClient

NOW = datetime(2026, 10, 30, 19, 0, tzinfo=ZoneInfo("Asia/Seoul"))
DM = Scope(None, 10, 100)
SERVER = Scope(1, 20, 100, True)


def _annotate(store, item_id, kind=MemoryKind.FACT, **fields):
    # Ambient needs quoted evidence; without it the parser keeps the item contextual.
    cited = [{"user": fields.get("evidence", ""), "at": "2026-10-20 03:00:00"}]
    store.upsert_memory_annotation(
        replace(parse_annotation(fields, kind, cited), memory_item_id=item_id)
    )


def _store():
    store = Store.create(":memory:")
    ids = {
        "food": store.add_memory_item(DM, "매운 음식을 좋아함", kind=MemoryKind.PREFERENCE,
                                      disclosure=MemoryDisclosure.LOCAL),
        "work": store.add_memory_item(DM, "사내 결제 시스템 마이그레이션 담당",
                                      kind=MemoryKind.FACT, disclosure=MemoryDisclosure.LOCAL),
        "cat": store.add_memory_item(DM, "고양이 보리를 키움", kind=MemoryKind.FACT,
                                     disclosure=MemoryDisclosure.LOCAL),
        "boundary": store.add_memory_item(DM, "가족 얘기는 먼저 꺼내지 말아 달라고 함",
                                          kind=MemoryKind.BOUNDARY,
                                          disclosure=MemoryDisclosure.LOCAL),
        "relationship": store.add_memory_item(DM, "가벼운 장난을 주고받음",
                                              kind=MemoryKind.RELATIONSHIP,
                                              disclosure=MemoryDisclosure.IMPLICIT,
                                              relationship_evidence={"comfort": 2}),
    }
    _annotate(store, ids["food"], MemoryKind.PREFERENCE, evidence="매운 음식 좋아",
              time_scope="ongoing", stability="long", usage_mode="ambient",
              keywords=["음식", "매운", "배고픔"])
    _annotate(store, ids["work"], time_scope="ongoing", stability="long",
              usage_mode="contextual", keywords=["회사", "결제", "마이그레이션"])
    _annotate(store, ids["cat"], evidence="고양이 보리 키워", time_scope="ongoing",
              stability="long", usage_mode="ambient", importance=3, keywords=["고양이", "보리"])
    return store, ids


def _run(store, scope, message):
    items = store.memory_items(100)
    annotations = store.memory_annotations([item.id for item in items])
    ctx = SelectionContext(scope=scope, message=message, now=NOW, hours_since_last_turn=1)
    return MemoryReadRun(
        "shadow", select_personal_memory(items, annotations, ctx), message=message, now=NOW,
        legacy_notes=legacy_notes(items, annotations, ctx),
    )


def test_legacy_notes_mirror_the_legacy_projection_without_directives():
    store, ids = _store()

    dm = {item.id for item, _annotation in _run(store, DM, "안녕").legacy_notes}
    server = _run(store, SERVER, "안녕").legacy_notes

    # Relationship items use the profile path; directives always reach T0.
    assert dm == {ids["food"], ids["work"], ids["cat"]}
    # DM-local items are not FULL in a server, so legacy never sent them there.
    assert server == ()
    store.close()


def test_coverage_counts_used_covered_and_missed_notes():
    store, ids = _store()
    run = _run(store, DM, "아 배고프다")
    assert [entry.item.id for entry in run.selection.selected
            if entry.placement == "notes"] == [ids["food"]]

    coverage = shadow_coverage(run, "매운 거 먹자! 보리는 밥 먹었어? 회사 결제 건은 끝났고?")

    # The cat is importance-3 daily life, so the core profile covers it without a match.
    assert (coverage.notes, coverage.used, coverage.covered) == (3, 3, 2)
    assert (coverage.covered_profile, coverage.covered_carry) == (1, 0)
    assert coverage.missed_item_ids == (ids["work"],)
    # The missed note was not in the user's message: the answer recalled it unprompted.
    assert coverage.missed_unprompted == 1
    assert coverage.missed_current == 1
    assert coverage.missed_usage == {"contextual": 1}
    assert coverage.missed_importance == {"2": 1}
    store.close()


def test_coverage_counts_notes_covered_by_a_carried_topic():
    store, ids = _store()
    items = store.memory_items(100)
    annotations = store.memory_annotations([item.id for item in items])
    ctx = SelectionContext(scope=DM, message="이거 언제 끝나냐", now=NOW, hours_since_last_turn=1,
                           recent_own_texts=("회사 결제 건 때문에 야근 중",))
    run = MemoryReadRun("shadow", select_personal_memory(items, annotations, ctx),
                        message=ctx.message, now=NOW,
                        legacy_notes=legacy_notes(items, annotations, ctx))

    coverage = shadow_coverage(run, "결제 건 오늘 안에 끝나면 좋겠다")

    assert coverage.missed == 0
    assert (coverage.covered_profile, coverage.covered_carry) == (0, 1)
    assert [entry.via for entry in run.selection.selected if entry.item.id == ids["work"]] == [
        "carry"
    ]
    store.close()


def test_unused_notes_and_echoed_keywords_are_not_unprompted_misses():
    store, _ids = _store()
    run = _run(store, DM, "회사 너무 피곤해")

    assert shadow_coverage(run, "푹 쉬어").used == 0
    coverage = shadow_coverage(run, "회사 일 많았구나")
    # The user raised work, so tiered selected it: covered, not missed.
    assert (coverage.used, coverage.covered, coverage.missed) == (1, 1, 0)
    store.close()


def _client(store, rows):
    return NS(
        store=store,
        events=NS(emit=lambda event, **fields: rows.append((event, fields))),
        llm=NS(usage=NS(routing_event=lambda operation, **fields: rows.append(
            (operation, fields)
        ))),
    )


def test_bot_records_shadow_coverage_without_touching_memory():
    store, ids = _store()
    rows = []
    run = replace(_run(store, DM, "아 배고프다"), visual_turn=True)
    CURRENT_MEMORY_SELECTION.set(run)
    CURRENT_CONTEXT_PROVENANCE.set({"memory_selection": {"mode": "shadow", "tiers": {"T2": 1}}})

    HinaClient._record_memory_usage(
        _client(store, rows), "매운 거 먹자. 보리는 잘 있어? 회사 결제 건은?", "dm",
    )

    # Shadow never writes usage metadata: cooldown and follow-ups stay untouched.
    assert all(store.memory_annotation(item_id).last_used_at is None
               for item_id in (ids["food"], ids["cat"]))
    assert CURRENT_MEMORY_SELECTION.get() is None
    coverage = CURRENT_CONTEXT_PROVENANCE.get()["memory_selection"]["coverage"]
    assert coverage["missed_item_ids"] == [ids["work"]]
    assert coverage["visual_turn"] is True and coverage["recent_own_turn"] is False
    [(operation, fields)] = rows
    assert operation == "memory.shadow_coverage"
    assert fields["memory_coverage_used"] == 3
    assert fields["memory_coverage_missed"] == 1
    assert fields["memory_coverage_missed_contextual"] == 1
    assert fields["memory_coverage_covered_profile"] == 1
    assert fields["memory_space"] == "dm" and fields["memory_visual_turn"] is True
    serialized = repr(fields) + repr(coverage)
    assert "고양이" not in serialized and "결제" not in serialized
    store.close()


def test_bot_shadow_coverage_failure_never_raises():
    store, _ids = _store()
    rows = []
    CURRENT_MEMORY_SELECTION.set(MemoryReadRun("shadow", selection=None, legacy_notes=()))

    HinaClient._record_memory_usage(_client(store, rows), "응", "dm")

    assert [event for event, _fields in rows] == ["memory.shadow_coverage_failed"]
    store.close()


def test_recent_own_texts_are_this_users_recent_turns_here():
    store = Store.create(":memory:")
    other = Scope(None, 10, 200)
    for message_id, text in enumerate(("첫 번째", "어제 고양이 병원 다녀왔어", "사료를 바꿨어"), 1):
        store.record_completed_turn(DM, message_id, text, "응", name="사용자")
    store.record_completed_turn(other, 9, "다른 사람 이야기", "응", name="다른 사람")
    now = datetime.now(UTC)

    assert recent_own_texts(store, DM, now) == ("어제 고양이 병원 다녀왔어", "사료를 바꿨어")
    assert recent_own_texts(store, DM, now + timedelta(hours=1)) == ()
    assert recent_own_texts(store, Scope(1, 10, 100), now) == ()
    store.close()


def test_recent_own_texts_skip_hina_replies_and_other_members_in_a_server():
    store = Store.create(":memory:")
    speaker, other = Scope(1, 20, 100, True), Scope(1, 20, 200, True)
    store.record_completed_turn(speaker, 1, "오늘 피곤하다", "고양이 보리는 잘 있어?", name="A")
    store.record_completed_turn(other, 2, "우리 고양이 귀엽지", "응", name="B")
    store.record_completed_turn(Scope(1, 21, 100, True), 3, "다른 채널 이야기", "응", name="A")

    # Only the speaker's own words in this channel: never Hina's reply or another member.
    assert recent_own_texts(store, speaker, datetime.now(UTC)) == ("오늘 피곤하다",)
    store.close()


def test_visual_turn_counts_only_photos_sent_or_replied_to_now():
    def visual(source, context_kind="current_message"):
        return VisualInput(b"x", "image/png", source, context_kind=context_kind)

    assert _visual_turn((visual("attachment"),))
    assert _visual_turn((visual("attachment", "replied_message"),))
    assert not _visual_turn((visual("sticker"), visual("emoji")))
    assert not _visual_turn((visual("attachment", "recent_channel_message"),))
    assert not _visual_turn(())
