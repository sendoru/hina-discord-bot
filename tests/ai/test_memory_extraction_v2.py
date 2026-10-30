"""Extraction v2 (#451): annotations, verification, reconfirmation and suppression."""

import json
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_extraction import (
    ExtractedMemoryItem,
    apply_directive_verification,
    build_shadow_turns,
    parse_annotation,
    parse_shadow_extraction,
    persist_shadow_items_detailed,
)
from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.core.memory_annotations import (
    LINT_AUDIENCE_NARROWED,
    LINT_DEFAULTED,
    LINT_DIRECTIVE_REJECTED,
    LINT_DIRECTIVE_UNVERIFIED,
    LINT_DISCLOSURE_DEFAULTED,
    LINT_EVIDENCE_MISMATCH,
    LINT_INVALID_EVENT_AT,
    LINT_INVALID_RECURRENCE,
    LINT_KIND_DEFAULTED,
    LINT_PAST_MARKER,
    LINT_UNDATED_EVENT,
    Annotator,
    Audience,
    DirectiveType,
    Stability,
    TimeScope,
    UsageMode,
)
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind, RelationshipEvidence
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

AT = "2026-10-30 03:00:00"


def _settings():
    return NS(
        structured_memory_every=4, model="fixed-model", model_routing_mode="fixed",
        memory_routing_smart_threshold=2.0, memory_output_tokens=4096,
        gemini_thinking_level="low", provider="openai", runtime_timezone="Asia/Seoul",
    )


def _harness(*responses):
    harness = MemorySummaryMixin()
    harness.settings = _settings()
    harness.client = object()
    harness.events = []
    harness.usage = NS(
        request=AsyncMock(side_effect=responses),
        routing_event=lambda operation, **metadata: harness.events.append((operation, metadata)),
    )
    return harness


def _response(payload, status="completed"):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return NS(status=status, output_text=text)


def _completed(harness):
    return [metadata for operation, metadata in harness.events
            if operation == "memory.shadow_extraction" and metadata.get("status") == "completed"][-1]


def _turns(store, scope, start, contents, *, at=AT):
    for offset, content in enumerate(contents):
        message_id = start + offset
        store.add(scope, message_id, content, f"reply-{message_id}")
        store.db.execute("UPDATE turns SET created_at=? WHERE message_id=?", (at, str(message_id)))
    store.db.commit()


def _item(content, source, **fields):
    return {"content": content, "kind": fields.pop("kind", "fact"), "disclosure": "local",
            "confidence": 0.9, "source_message_ids": [source], **fields}


CITED = [{"user": "나 다음 주 화요일에 면접 있어", "at": AT}]


def test_parse_annotation_keeps_valid_judgments():
    annotation = parse_annotation({
        "evidence": "다음 주 화요일에 면접", "time_scope": "planned", "event_at": "2026-11-03",
        "usage_mode": "ambient", "audience": "owner", "importance": 2, "slot": "interview",
        "keywords": ["면접", "면접", " 취업 "], "explicit": False, "stability": "long",
    }, MemoryKind.FACT, CITED)

    assert annotation.time_scope == TimeScope.PLANNED
    assert annotation.event_at == "2026-11-03"
    assert annotation.stability is None  # stability only applies to ongoing items
    assert annotation.usage_mode == UsageMode.AMBIENT
    assert annotation.keywords == ("면접", "취업")
    assert annotation.observed_at == annotation.last_confirmed_at == AT
    assert annotation.annotator == Annotator.EXTRACTOR
    assert annotation.lint_flags == ()


def test_parse_annotation_demotes_unquoted_ambient_and_flags_it():
    annotation = parse_annotation({
        "evidence": "면접 일정이 있다고 했다", "time_scope": "planned", "event_at": "2026-11-03",
        "usage_mode": "ambient",
    }, MemoryKind.FACT, CITED)

    assert annotation.evidence == ""
    assert annotation.usage_mode == UsageMode.CONTEXTUAL
    assert LINT_EVIDENCE_MISMATCH in annotation.lint_flags


def test_parse_annotation_flags_past_marker_on_ongoing_state():
    annotation = parse_annotation({
        "evidence": "예전에 수영했었어", "time_scope": "ongoing", "stability": "long",
        "usage_mode": "ambient",
    }, MemoryKind.FACT, [{"user": "나 예전에 수영했었어", "at": AT}])

    assert annotation.usage_mode == UsageMode.CONTEXTUAL
    assert annotation.time_scope == TimeScope.ONGOING
    assert LINT_PAST_MARKER in annotation.lint_flags


def test_parse_annotation_rejects_malformed_dates_and_recurrence():
    annotation = parse_annotation({
        "evidence": "다음 주 화요일에 면접", "time_scope": "planned", "event_at": "next tuesday",
        "recurrence": "every tuesday", "usage_mode": "contextual",
    }, MemoryKind.FACT, CITED)

    assert annotation.event_at is None and annotation.recurrence is None
    assert {LINT_INVALID_EVENT_AT, LINT_INVALID_RECURRENCE, LINT_UNDATED_EVENT} <= set(
        annotation.lint_flags
    )


@pytest.mark.parametrize(("kind", "usage", "directive"), [
    (MemoryKind.BOUNDARY, UsageMode.DIRECTIVE, DirectiveType.BOUNDARY),
    (MemoryKind.FACT, UsageMode.CONTEXTUAL, None),
    (MemoryKind.PREFERENCE, UsageMode.CONTEXTUAL, None),
])
def test_missing_judgments_use_the_quiet_reading_except_boundaries(kind, usage, directive):
    annotation = parse_annotation({}, kind, CITED)

    assert (annotation.usage_mode, annotation.directive_type) == (usage, directive)
    assert annotation.time_scope == TimeScope.UNKNOWN
    assert LINT_DEFAULTED in annotation.lint_flags


def test_sensitive_items_never_reach_server_audience_and_explicit_raises_importance():
    annotation = parse_annotation({
        "evidence": "다음 주 화요일에 면접", "time_scope": "planned", "event_at": "2026-11-03",
        "usage_mode": "sensitive", "audience": "server", "importance": 1, "explicit": True,
    }, MemoryKind.FACT, CITED)

    assert annotation.audience == Audience.OWNER
    assert annotation.importance == 3
    assert LINT_AUDIENCE_NARROWED in annotation.lint_flags


def test_parser_annotates_only_non_relationship_items_when_turns_are_given():
    payload = {"items": [
        _item("사용자는 다음 주 화요일에 면접이 있다.", "101", evidence="다음 주 화요일에 면접",
              time_scope="planned", event_at="2026-11-03", usage_mode="contextual"),
        _item("사용자는 히나와 편하게 대화한다.", "101", kind="relationship",
              relationship_evidence={"comfort": 2}),
    ]}
    turns = {"101": CITED[0]}

    plain = parse_shadow_extraction(json.dumps(payload, ensure_ascii=False),
                                    allowed_source_ids={"101"})
    annotated = parse_shadow_extraction(json.dumps(payload, ensure_ascii=False),
                                        allowed_source_ids={"101"}, source_turns=turns)

    assert [item.annotation for item in plain.items] == [None, None]
    assert annotated.items[0].annotation.event_at == "2026-11-03"
    assert annotated.items[1].annotation is None


def test_shadow_turns_carry_local_time_and_weekday():
    turns, *_ = build_shadow_turns(
        [{"message_id": "1", "created_at": AT, "content": "hi", "reply": "", "exportable": 0}],
        include_replies=True, zone_name="Asia/Seoul",
    )

    assert turns[0]["at_local"] == "2026-10-30T12:00+09:00"
    assert turns[0]["weekday"] == "FRI"


def _directive(content, directive_type):
    annotation = parse_annotation({
        "evidence": "그 얘기는 꺼내지 마", "time_scope": "ongoing", "stability": "long",
        "usage_mode": "directive", "directive_type": directive_type,
    }, MemoryKind.BOUNDARY, [{"user": "그 얘기는 꺼내지 마", "at": AT}])
    return ExtractedMemoryItem(content, MemoryKind.BOUNDARY, MemoryDisclosure.LOCAL, 0.9, ("1",),
                               annotation=annotation)


@pytest.mark.parametrize(("verdict", "usage", "verified", "flag"), [
    ("keep", UsageMode.DIRECTIVE, True, None),
    ("reject", UsageMode.CONTEXTUAL, False, LINT_DIRECTIVE_REJECTED),
    (None, UsageMode.DIRECTIVE, False, LINT_DIRECTIVE_UNVERIFIED),
])
def test_directive_verification_outcomes(verdict, usage, verified, flag):
    item = _directive("사용자는 전 직장 얘기를 원하지 않는다.", "boundary")
    text = json.dumps({"results": [{"index": 0, "verdict": verdict}]}) if verdict else "not json"

    (updated,), checked_verified, demoted = apply_directive_verification((item,), text)

    assert updated.annotation.usage_mode == usage
    assert updated.annotation.verified is verified
    assert (flag in updated.annotation.lint_flags) if flag else not updated.annotation.lint_flags
    assert (checked_verified, demoted) == (int(verified), int(verdict == "reject"))


def test_temporary_boundary_is_short_lived_but_temporary_style_is_rejected():
    boundary = _directive("오늘은 시험 얘기를 하지 말아 달라고 했다.", "boundary")
    style = _directive("사용자는 반말을 원한다.", "style")
    text = json.dumps({"results": [{"index": 0, "verdict": "temporary"},
                                   {"index": 1, "verdict": "temporary"}]})

    (short, rejected), verified, demoted = apply_directive_verification((boundary, style), text)

    assert short.annotation.stability == Stability.SHORT and short.annotation.verified
    assert rejected.annotation.usage_mode == UsageMode.CONTEXTUAL
    assert (verified, demoted) == (1, 1)


@pytest.mark.asyncio
async def test_extraction_stores_annotation_next_to_item_in_dm():
    store = Store.create(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _turns(store, scope, 101, ["나 다음 주 화요일에 면접 있어", "b", "c", "d"])
    harness = _harness(_response({"items": [_item(
        "사용자는 2026-11-03에 면접이 있다.", "101", evidence="다음 주 화요일에 면접",
        time_scope="planned", event_at="2026-11-03", usage_mode="contextual", audience="server",
        slot="interview",
    )]}))

    assert await harness.extract_structured_memory(store, scope)

    request = json.loads(harness.usage.request.await_args.kwargs["input"])
    assert request["turns"][0]["weekday"] == "FRI"
    (item,) = store.memory_items(100)
    annotation = store.memory_annotation(item.id)
    assert annotation.event_at == "2026-11-03"
    assert annotation.observed_at == AT
    assert annotation.audience == Audience.OWNER  # DM origin never reaches the server audience
    assert LINT_AUDIENCE_NARROWED in annotation.lint_flags
    assert _completed(harness)["annotated_items"] == 1
    assert [call.args[1] for call in harness.usage.request.await_args_list] == [
        "extract_memory_items_shadow"
    ]
    store.close()


@pytest.mark.asyncio
async def test_directive_items_get_one_verification_call_before_commit():
    store = Store.create(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _turns(store, scope, 101, ["앞으로 전 직장 얘기는 꺼내지 마", "b", "c", "d"])
    harness = _harness(
        _response({"items": [_item(
            "사용자는 전 직장 얘기를 원하지 않는다.", "101", kind="boundary",
            evidence="전 직장 얘기는 꺼내지 마", time_scope="ongoing", stability="long",
            usage_mode="directive", directive_type="boundary",
        )]}),
        _response({"results": [{"index": 0, "verdict": "keep"}]}),
    )

    assert await harness.extract_structured_memory(store, scope)

    calls = harness.usage.request.await_args_list
    assert [call.args[1] for call in calls] == [
        "extract_memory_items_shadow", "verify_memory_directives"
    ]
    verification = json.loads(calls[1].kwargs["input"])
    assert verification["items"][0]["source_turns"][0]["user"] == "앞으로 전 직장 얘기는 꺼내지 마"
    (item,) = store.memory_items(100)
    assert store.memory_annotation(item.id).verified
    completed = _completed(harness)
    assert (completed["directive_items"], completed["verified_directives"]) == (1, 1)
    store.close()


@pytest.mark.asyncio
async def test_verification_failure_keeps_boundary_unverified():
    store = Store.create(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    _turns(store, scope, 101, ["앞으로 전 직장 얘기는 꺼내지 마", "b", "c", "d"])
    harness = _harness(
        _response({"items": [_item("사용자는 전 직장 얘기를 원하지 않는다.", "101", kind="boundary")]}),
        RuntimeError("provider down"),
    )

    assert await harness.extract_structured_memory(store, scope)

    (item,) = store.memory_items(100)
    annotation = store.memory_annotation(item.id)
    assert annotation.usage_mode == UsageMode.DIRECTIVE
    assert not annotation.verified
    assert LINT_DIRECTIVE_UNVERIFIED in annotation.lint_flags
    store.close()


@pytest.mark.asyncio
async def test_same_space_remention_reconfirms_instead_of_storing_again():
    store = Store.create(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    item = {"evidence": "a", "time_scope": "ongoing", "stability": "long", "usage_mode": "ambient"}
    _turns(store, scope, 101, ["a", "b", "c", "d"])
    _turns(store, scope, 105, ["a", "b", "c", "d"], at="2026-11-02 03:00:00")
    harness = _harness(
        _response({"items": [_item("사용자는 고양이를 키운다.", "101", **item)]}),
        _response({"items": [_item("사용자는 고양이를 키운다.", "105", **item)]}),
    )

    await harness.extract_structured_memory(store, scope)
    await harness.extract_structured_memory(store, scope)

    (stored,) = store.memory_items(100)
    annotation = store.memory_annotation(stored.id)
    assert annotation.confirmations == 2
    assert annotation.last_confirmed_at == "2026-11-02 03:00:00"
    assert annotation.observed_at == AT
    assert _completed(harness)["reconfirmed_items"] == 1
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("slot", "applied"), [("pet_name", True), ("pet_breed", False)])
async def test_same_slot_correction_uses_lower_threshold(slot, applied):
    store = Store.create(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    old_id = store.add_memory_item(scope, "사용자의 고양이 이름은 키위다.", kind=MemoryKind.FACT,
                                   disclosure=MemoryDisclosure.LOCAL, source_message_ids=("90",),
                                   confidence=0.9)
    old = parse_annotation({"evidence": "x", "time_scope": "ongoing", "usage_mode": "contextual",
                            "slot": "pet_name"}, MemoryKind.FACT, [{"user": "x", "at": AT}])
    store.upsert_memory_annotation(replace(old, memory_item_id=old_id))
    _turns(store, scope, 101, ["고양이 이름 웅으로 바꿨어", "b", "c", "d"])
    harness = _harness(_response({"items": [_item(
        "사용자의 고양이 이름은 웅이다.", "101", evidence="고양이 이름 웅으로 바꿨어",
        time_scope="ongoing", usage_mode="contextual", slot=slot,
        relation={"type": "corrects", "target_item_id": old_id, "confidence": 0.85},
    )]}))

    await harness.extract_structured_memory(store, scope)

    candidates = json.loads(harness.usage.request.await_args.kwargs["input"])[
        "existing_memory_candidates"
    ]
    assert candidates[0]["slot"] == "pet_name" and candidates[0]["time_scope"] == "ongoing"
    active = [item.content for item in store.memory_items(100)]
    assert ("사용자의 고양이 이름은 키위다." not in active) is applied
    store.close()


def _relationship(axes):
    return ExtractedMemoryItem("사용자는 히나와 편하게 대화한다.", MemoryKind.RELATIONSHIP,
                               MemoryDisclosure.IMPLICIT, 0.9, ("201",),
                               relationship_evidence=RelationshipEvidence(**axes))


@pytest.mark.parametrize(("observed_at", "axes", "suppressed"), [
    ("2026-11-05 03:00:00", {"comfort": 2}, True),
    ("2026-11-05 03:00:00", {"comfort": 3}, False),  # a stronger signal is new evidence
    ("2026-11-20 03:00:00", {"comfort": 2}, False),  # outside the 14-day window
])
def test_covered_relationship_observation_is_suppressed(observed_at, axes, suppressed):
    store = Store.create(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    first = _relationship({"comfort": 2, "familiarity": 1})
    persist_shadow_items_detailed(store, scope, (replace(first, source_message_ids=("200",)),))
    store.db.execute("UPDATE memory_items SET created_at=?", (AT,))
    store.db.commit()

    result = persist_shadow_items_detailed(
        store, scope, (_relationship(axes),), batch_observed_at=observed_at,
    )

    assert result.suppressed_relationships == int(suppressed)
    assert len(store.memory_items(100)) == (1 if suppressed else 2)
    store.close()


def test_admin_annotation_is_not_overwritten_and_relationships_are_not_annotated():
    store = Store.create(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    fact_id = store.add_memory_item(scope, "사실", kind=MemoryKind.FACT,
                                    disclosure=MemoryDisclosure.LOCAL, source_message_ids=("1",),
                                    confidence=0.9)
    relation_id = store.add_memory_item(scope, "관계", kind=MemoryKind.RELATIONSHIP,
                                        disclosure=MemoryDisclosure.IMPLICIT,
                                        source_message_ids=("1",), confidence=0.9)
    base = replace(parse_annotation({}, MemoryKind.FACT, CITED), memory_item_id=fact_id)
    store.upsert_memory_annotation(replace(base, annotator=Annotator.ADMIN, slot="admin"))
    store.upsert_memory_annotation(replace(base, slot="extractor"))

    assert store.memory_annotation(fact_id).slot == "admin"
    with pytest.raises(ValueError, match="Relationship"):
        store.upsert_memory_annotation(replace(base, memory_item_id=relation_id))
    store.close()


@pytest.mark.parametrize(("new_kind", "outcome"), [
    (MemoryKind.PREFERENCE, "applied"),  # fact/preference labels may disagree (decision 15)
    (MemoryKind.RELATIONSHIP, "kind_mismatch"),
])
def test_reconciliation_only_splits_on_the_relationship_boundary(new_kind, outcome):
    store = Store.create(":memory:", history_turns=12)
    scope = Scope(None, 10, 100)
    target = store.add_memory_item(scope, "커피를 못 마신다.", kind=MemoryKind.FACT,
                                   disclosure=MemoryDisclosure.LOCAL, source_message_ids=("1",),
                                   confidence=0.9)
    new = store.add_memory_item(scope, "커피를 마시지 못한다.", kind=new_kind,
                                disclosure=MemoryDisclosure.LOCAL, source_message_ids=("2",),
                                confidence=0.9)
    proposal = store.add_memory_reconciliation_proposal(
        scope, new_memory_item_id=new, target_memory_item_id=target, relation="duplicate",
        confidence=0.99, source_message_ids=("2",),
    )

    assert store.apply_memory_reconciliation_proposal(proposal) == outcome
    store.close()


def test_parser_accepts_a_fenced_bare_items_array():
    """Gemini sometimes drops the {"items": ...} envelope; items are still validated."""
    rows = [
        _item("사용자는 2026-11-03에 면접이 있다.", "101", evidence="다음 주 화요일에 면접",
              time_scope="planned", event_at="2026-11-03", usage_mode="contextual"),
        _item("근거 없는 항목", "999"),
    ]
    text = "```json\n" + json.dumps(rows, ensure_ascii=False, indent=2) + "\n```"

    parsed = parse_shadow_extraction(text, allowed_source_ids={"101"},
                                     source_turns={"101": CITED[0]})

    assert parsed.valid and parsed.rejected_items == 1
    (item,) = parsed.items
    assert item.annotation.event_at == "2026-11-03"


@pytest.mark.parametrize(("text", "valid"), [
    ("[]", True),
    ('{"items": []}', True),
    ('{"memories": []}', False),
    ('"items"', False),
    ("[1, 2]", True),  # rows are still rejected one by one
])
def test_parser_relaxes_only_the_envelope(text, valid):
    parsed = parse_shadow_extraction(text, allowed_source_ids={"101"})

    assert parsed.valid is valid
    assert parsed.items == ()


def test_verifier_accepts_a_bare_results_array():
    item = _directive("사용자는 전 직장 얘기를 원하지 않는다.", "boundary")

    (updated,), verified, demoted = apply_directive_verification(
        (item,), '```json\n[{"index": 0, "verdict": "keep"}]\n```'
    )

    assert updated.annotation.verified and (verified, demoted) == (1, 0)


@pytest.mark.parametrize(("value", "valid"), [
    ("weekly:TUE 19:00", True),
    ("weekly:TUE", True),  # "매주 화요일 저녁": weekday without a clock time
    ("monthly:15", True),
    ("weekly:tue", False),
    ("weekly:TUE 24:00", False),
    ("weekly:TUE evening", False),
])
def test_recurrence_time_is_optional(value, valid):
    annotation = parse_annotation({
        "evidence": "매주 화요일 저녁에 PT", "time_scope": "recurring", "recurrence": value,
        "usage_mode": "ambient",
    }, MemoryKind.FACT, [{"user": "나 매주 화요일 저녁에 PT 받아", "at": AT}])

    assert (annotation.recurrence == value) is valid
    assert (LINT_INVALID_RECURRENCE in annotation.lint_flags) is not valid


def test_invalid_disclosure_copied_from_usage_is_kept_as_local():
    """Live eval: "disclosure":"sensitive" used to drop the whole (valid) item."""
    raw = {"items": [_item(
        "보리가 피부 알레르기가 있어 요즘 동물병원에 다님", "101", disclosure="sensitive",
        evidence="우리 보리가 피부 알레르기", time_scope="ongoing", usage_mode="ambient",
    )]}
    turns = {"101": {"user": "우리 보리가 피부 알레르기가 있어서 요즘 동물병원 다니는 중이야", "at": AT}}

    parsed = parse_shadow_extraction(json.dumps(raw, ensure_ascii=False),
                                     allowed_source_ids={"101"}, source_turns=turns)

    (item,) = parsed.items
    assert parsed.rejected_items == 0
    assert item.disclosure == MemoryDisclosure.LOCAL
    assert LINT_DISCLOSURE_DEFAULTED in item.annotation.lint_flags


@pytest.mark.parametrize(("row", "accepted"), [
    ({"kind": "directive"}, True),  # legacy label only: kept as fact
    ({"kind": None}, True),
    ({"kind": "mood", "relationship_evidence": {"comfort": 2}}, False),  # maybe relationship
])
def test_unknown_kind_falls_back_unless_it_may_be_a_relationship(row, accepted):
    raw = {"items": [{**_item("회사 얘기를 먼저 꺼내지 않기", "101"), **row}]}

    parsed = parse_shadow_extraction(json.dumps(raw, ensure_ascii=False),
                                     allowed_source_ids={"101"}, source_turns={"101": CITED[0]})

    assert (len(parsed.items), parsed.rejected_items) == ((1, 0) if accepted else (0, 1))
    if accepted:
        assert parsed.items[0].kind == MemoryKind.FACT
        assert LINT_KIND_DEFAULTED in parsed.items[0].annotation.lint_flags


def test_relationship_with_invalid_disclosure_is_kept_without_annotation():
    raw = {"items": [_item("사용자는 히나와 편하게 대화한다.", "101", kind="relationship",
                           disclosure="sensitive", relationship_evidence={"comfort": 2})]}

    parsed = parse_shadow_extraction(json.dumps(raw, ensure_ascii=False),
                                     allowed_source_ids={"101"}, source_turns={"101": CITED[0]})

    (item,) = parsed.items
    assert item.kind == MemoryKind.RELATIONSHIP and item.disclosure == MemoryDisclosure.LOCAL
    assert item.annotation is None
