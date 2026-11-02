"""Same-space duplicates reinforce evidence-less annotations (#471, #449 principles 2 and 6)."""

from dataclasses import replace

import pytest

from hina_bot.ai.memory_extraction import (
    ANNOTATOR_VERSION,
    ExtractedMemoryItem,
    MemoryRelationProposal,
    parse_annotation,
    persist_reconciliation_proposals,
    persist_shadow_items_detailed,
)
from hina_bot.core.memory_annotations import (
    LINT_AUDIENCE_NARROWED,
    LINT_DIRECTIVE_UNVERIFIED,
    LINT_SOURCE_UNAVAILABLE,
    Annotator,
    Audience,
    DirectiveType,
    MemoryAnnotation,
    Stability,
    TimeScope,
    UsageMode,
    without_evidence,
)
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind, MemoryStatus
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

DM = Scope(None, 10, 100)
GUILD = Scope(1, 10, 100, True)
OTHER_PUBLIC_CHANNEL = Scope(1, 11, 100, True)
PRIVATE_CHANNEL = Scope(1, 10, 100, False)

OLD = "2026-09-01 00:00:00"
NEW = "2026-11-02 03:00:00"
QUOTE = "고양이 키워"


@pytest.fixture
def store():
    store = Store.create(":memory:", history_turns=12)
    yield store
    store.close()


def _item(store, scope, content="사용자는 고양이를 키운다."):
    return store.add_memory_item(
        scope, content, kind=MemoryKind.FACT, disclosure=MemoryDisclosure.LOCAL,
        source_message_ids=("1",), confidence=0.9,
    )


def _annotate(store, item_id, **fields):
    """A backfilled annotation: no quote, quiet reading, markers from earlier use."""

    base = MemoryAnnotation(
        memory_item_id=item_id, item_revision=0, time_scope=TimeScope.UNKNOWN,
        observed_at=OLD, last_confirmed_at=OLD, usage_mode=UsageMode.CONTEXTUAL,
        annotator=Annotator.BACKFILL, annotator_version="backfill-v1",
        lint_flags=(LINT_SOURCE_UNAVAILABLE,), last_used_at="2026-10-30 00:00:00",
        proactive_status="asked", proactive_count=1, last_proactive_at="2026-10-30 00:00:00",
    )
    store.upsert_memory_annotation(replace(base, **fields))
    return store.memory_annotation(item_id)


def _remention(
    store, scope, target_id, *, confidence=0.99, verified=False,
    content="사용자는 고양이 보리를 키운다.", **raw,
):
    """Persist a paraphrased re-mention the way extraction does, then apply its proposal."""

    fields = {
        "evidence": QUOTE, "time_scope": "ongoing", "stability": "long", "usage_mode": "ambient",
        "importance": 3, "slot": "pet", "keywords": ["고양이", "보리"], **raw,
    }
    annotation = replace(
        parse_annotation(fields, MemoryKind.FACT, [{"user": "나 고양이 키워", "at": NEW}]),
        verified=verified,
    )
    item = ExtractedMemoryItem(
        content, MemoryKind.FACT, MemoryDisclosure.LOCAL, 0.9, ("501",),
        relation=MemoryRelationProposal("duplicate", target_id, confidence), annotation=annotation,
    )
    result = persist_shadow_items_detailed(store, scope, (item,))
    outcome = persist_reconciliation_proposals(store, scope, (item,), result.item_ids)
    return outcome, result.item_ids[0]


def test_duplicate_gives_a_backfilled_target_the_judgment_and_quote(store):
    target = _item(store, DM)
    before = _annotate(store, target)

    outcome, duplicate = _remention(store, DM, target)

    after = store.memory_annotation(target)
    assert (outcome.applied, outcome.reinforced, outcome.reconfirmed) == (1, 1, 1)
    assert (after.usage_mode, after.importance, after.time_scope, after.stability) == (
        UsageMode.AMBIENT, 3, TimeScope.ONGOING, Stability.LONG,
    )
    assert (after.evidence, after.slot, after.keywords) == (QUOTE, "pet", ("고양이", "보리"))
    assert (after.annotator, after.annotator_version) == (Annotator.EXTRACTOR, ANNOTATOR_VERSION)
    assert after.lint_flags == ()
    # Observation and use history stay with the target; only the confirmation moves.
    assert after.observed_at == before.observed_at
    assert (after.last_used_at, after.proactive_status, after.proactive_count) == (
        before.last_used_at, before.proactive_status, before.proactive_count,
    )
    assert (after.confirmations, after.last_confirmed_at) == (2, NEW)
    # The quote stays traceable: the duplicate is a superseded row with its own annotation.
    moved = store.memory_item(duplicate)
    assert (moved.status, moved.superseded_by) == (MemoryStatus.SUPERSEDED, target)
    assert store.memory_annotation(duplicate).evidence == QUOTE


def test_reinforcement_follows_the_targets_current_revision(store):
    target = _item(store, DM)
    _annotate(store, target)
    store.revise_memory_item(
        target, expected_revision=0, content="사용자는 고양이를 키운다(수정).",
        kind="fact", disclosure="local", confidence=0.9,
    )
    assert store.memory_annotation(target).is_stale(1)

    _remention(store, DM, target)

    assert not store.memory_annotation(target).is_stale(1)


def test_a_public_memory_from_another_channel_is_reinforced_in_the_same_realm(store):
    target = _item(store, OTHER_PUBLIC_CHANNEL)
    _annotate(store, target)

    outcome, _ = _remention(store, GUILD, target, audience="server")

    after = store.memory_annotation(target)
    assert outcome.reinforced == 1
    assert (after.usage_mode, after.audience) == (UsageMode.AMBIENT, Audience.SERVER)


def test_a_dm_duplicate_of_a_server_memory_only_reconfirms(store):
    target = _item(store, OTHER_PUBLIC_CHANNEL)
    before = _annotate(store, target)

    outcome, _ = _remention(store, DM, target)

    after = store.memory_annotation(target)
    assert (outcome.applied, outcome.reinforced, outcome.reconfirmed) == (1, 0, 1)
    assert replace(after, confirmations=1, last_confirmed_at=OLD, updated_at="") == replace(
        before, updated_at=""
    )
    assert after.confirmations == 2


def test_a_duplicate_below_the_threshold_changes_nothing(store):
    target = _item(store, DM)
    before = _annotate(store, target)

    outcome, _ = _remention(store, DM, target, confidence=0.5)

    assert (outcome.applied, outcome.deferred, outcome.reinforced) == (0, 1, 0)
    assert store.memory_annotation(target) == before


def test_a_target_the_admin_judged_is_never_reinforced(store):
    target = _item(store, DM)
    _annotate(store, target, annotator=Annotator.ADMIN, annotator_version="dashboard",
              usage_mode=UsageMode.SENSITIVE, lint_flags=())

    outcome, _ = _remention(store, DM, target)

    after = store.memory_annotation(target)
    assert (outcome.reinforced, outcome.reconfirmed) == (0, 1)
    assert (after.usage_mode, after.annotator, after.evidence) == (
        UsageMode.SENSITIVE, Annotator.ADMIN, "",
    )


def test_a_target_that_already_has_a_quote_keeps_its_judgment(store):
    target = _item(store, DM)
    _annotate(store, target, annotator=Annotator.EXTRACTOR, evidence="원래 인용",
              usage_mode=UsageMode.CONTEXTUAL, importance=1, lint_flags=())

    outcome, _ = _remention(store, DM, target)

    after = store.memory_annotation(target)
    assert (outcome.reinforced, outcome.reconfirmed) == (0, 1)
    assert (after.evidence, after.usage_mode, after.importance) == ("원래 인용", UsageMode.CONTEXTUAL, 1)


def test_a_duplicate_without_a_verified_quote_cannot_reinforce(store):
    target = _item(store, DM)
    before = _annotate(store, target)

    # The model quoted words the user never said, so the parser dropped the quote.
    outcome, _ = _remention(store, DM, target, evidence="말한 적 없음")

    assert outcome.reinforced == 0
    assert store.memory_annotation(target).usage_mode == before.usage_mode


BOUNDARY = (UsageMode.DIRECTIVE, DirectiveType.BOUNDARY)
STYLE = (UsageMode.DIRECTIVE, DirectiveType.STYLE)
QUIET = (UsageMode.CONTEXTUAL, None)


@pytest.mark.parametrize(("target", "new", "verified", "reinforced"), [
    (QUIET, BOUNDARY, True, True),       # a verified directive may raise a quiet memory
    (QUIET, BOUNDARY, False, False),     # an unverified one may not
    (BOUNDARY, BOUNDARY, True, True),
    (BOUNDARY, BOUNDARY, False, False),
    (BOUNDARY, (UsageMode.AMBIENT, None), False, False),  # never demote a directive
    (BOUNDARY, STYLE, True, False),      # a boundary is not traded for a style directive
    (STYLE, BOUNDARY, True, True),
    (STYLE, (UsageMode.AMBIENT, None), False, False),  # a style directive is not demoted either
])
def test_directive_changes_need_verification_and_never_demote(
    store, target, new, verified, reinforced
):
    target_id = _item(store, DM)
    _annotate(store, target_id, usage_mode=target[0], directive_type=target[1])
    raw = {"usage_mode": new[0].value}
    if new[1] is not None:
        raw["directive_type"] = new[1].value

    outcome, _ = _remention(store, DM, target_id, verified=verified, **raw)

    after = store.memory_annotation(target_id)
    assert outcome.reinforced == int(reinforced)
    assert outcome.reconfirmed == 1
    assert (after.usage_mode, after.directive_type) == (new if reinforced else target)
    assert bool(after.evidence) is reinforced


def test_audience_is_narrowed_to_the_targets_origin(store):
    # The target was captured in a channel that was private then; the duplicate arrives
    # from the same channel after it became public, with a server-audience judgment.
    target = _item(store, PRIVATE_CHANNEL)
    _annotate(store, target)

    outcome, duplicate = _remention(store, GUILD, target, audience="server")

    assert store.memory_annotation(duplicate).audience == Audience.SERVER
    after = store.memory_annotation(target)
    assert outcome.reinforced == 1
    assert after.audience == Audience.OWNER
    assert LINT_AUDIENCE_NARROWED in after.lint_flags


def test_only_a_duplicate_superseded_into_the_target_can_reinforce(store):
    target = _item(store, DM)
    before = _annotate(store, target)
    stranger = _item(store, DM, "사용자는 강아지를 키운다.")
    _annotate(store, stranger, evidence=QUOTE, annotator=Annotator.EXTRACTOR,
              usage_mode=UsageMode.AMBIENT, lint_flags=())
    other = _item(store, DM, "사용자는 앵무새를 키운다.")
    _annotate(store, other)
    _, merged_elsewhere = _remention(store, DM, other)

    def reinforce(source):
        return store.reinforce_memory_annotation(DM, target_item_id=target, evidence_item_id=source)

    assert not reinforce(stranger)          # active, never merged into anything
    assert not reinforce(merged_elsewhere)  # superseded, but into a different target
    assert not reinforce(target)
    assert store.memory_annotation(target) == before


def test_failed_extraction_batch_leaves_the_target_unchanged(store):
    target = _item(store, DM)
    before = _annotate(store, target)
    store.add(DM, 101, "나 고양이 키워", "reply")
    pending = store.pending_memory_extraction(DM)

    with (
        pytest.raises(RuntimeError, match="abort batch"),
        store.atomic_memory_batch(DM, pending, expected_cursor=0),
    ):
        outcome, _ = _remention(store, DM, target)
        assert outcome.reinforced == 1 and store.memory_annotation(target).evidence == QUOTE
        raise RuntimeError("abort batch")

    assert store.memory_annotation(target) == before
    assert [item.id for item in store.memory_items(100)] == [target]


# Deletion contract (§4.6): the quote is user text, so it never outlives its last source.

def _reinforced_pair(store, *, target_scope=DM, scope=DM):
    target = _item(store, target_scope)
    _annotate(store, target)
    outcome, duplicate = _remention(store, scope, target)
    assert outcome.reinforced == 1
    return target, duplicate


def _delete(store, item_id):
    store.delete_memory_item(item_id, expected_revision=store.memory_item(item_id).revision)


def _assert_quiet_again(annotation):
    assert annotation.evidence == ""
    assert annotation.usage_mode == UsageMode.CONTEXTUAL  # ambient needs a quote (principle 6)
    assert LINT_SOURCE_UNAVAILABLE in annotation.lint_flags
    assert annotation.importance == 3 and annotation.keywords == ("고양이", "보리")  # not the quote


def test_permanently_deleting_the_duplicate_removes_the_quote_from_the_target(store):
    target, duplicate = _reinforced_pair(store)

    _delete(store, duplicate)

    assert store.memory_item(target).status == MemoryStatus.ACTIVE
    _assert_quiet_again(store.memory_annotation(target))


def test_purging_the_duplicates_channel_removes_the_quote_from_a_surviving_target(store):
    target, _ = _reinforced_pair(store, target_scope=OTHER_PUBLIC_CHANNEL, scope=GUILD)

    store.purge_channel_memory(GUILD)

    assert store.memory_item(target).status == MemoryStatus.ACTIVE
    _assert_quiet_again(store.memory_annotation(target))


def test_a_quote_another_duplicate_still_carries_stays_until_the_last_one_goes(store):
    target, first = _reinforced_pair(store)
    # Same words again: nothing more to reinforce, but a second superseded row carries them.
    outcome, second = _remention(store, DM, target, content="사용자는 보리라는 고양이를 키운다.")
    assert outcome.reinforced == 0

    _delete(store, first)
    assert store.memory_annotation(target).evidence == QUOTE

    _delete(store, second)
    _assert_quiet_again(store.memory_annotation(target))


def test_deleting_an_unrelated_duplicate_keeps_the_targets_own_quote(store):
    target = _item(store, DM)
    _annotate(store, target, annotator=Annotator.EXTRACTOR, evidence="원래 인용",
              usage_mode=UsageMode.AMBIENT, lint_flags=())
    _, duplicate = _remention(store, DM, target)

    _delete(store, duplicate)

    after = store.memory_annotation(target)
    assert (after.evidence, after.usage_mode) == ("원래 인용", UsageMode.AMBIENT)


def test_deleting_the_target_itself_leaves_no_dangling_state(store):
    target, duplicate = _reinforced_pair(store)

    _delete(store, target)

    assert store.memory_annotation(target) is None
    assert store.memory_item(duplicate).superseded_by is None
    assert store.memory_annotation(duplicate).evidence == QUOTE


def test_an_admin_keeps_their_judgment_when_only_the_quote_is_deleted(store):
    target, duplicate = _reinforced_pair(store)
    store.set_admin_memory_annotation(target, expected_revision=0, fields={
        "time_scope": "ongoing", "stability": "long", "usage_mode": "ambient",
        "audience": "owner", "importance": 3, "slot": "pet", "keywords": ["고양이"],
    })
    assert store.memory_annotation(target).evidence == QUOTE

    _delete(store, duplicate)

    after = store.memory_annotation(target)
    assert (after.evidence, after.annotator, after.usage_mode) == (
        "", Annotator.ADMIN, UsageMode.AMBIENT,
    )
    assert after.lint_flags == ()


def test_losing_the_quote_never_silences_a_boundary():
    annotation = MemoryAnnotation(
        memory_item_id=1, item_revision=0, time_scope=TimeScope.ONGOING, observed_at=OLD,
        last_confirmed_at=OLD, usage_mode=UsageMode.DIRECTIVE, directive_type=DirectiveType.BOUNDARY,
        annotator=Annotator.EXTRACTOR, annotator_version=ANNOTATOR_VERSION,
        evidence=QUOTE, verified=True,
    )

    after = without_evidence(annotation)

    assert (after.usage_mode, after.directive_type) == (UsageMode.DIRECTIVE, DirectiveType.BOUNDARY)
    assert not after.verified
    assert {LINT_SOURCE_UNAVAILABLE, LINT_DIRECTIVE_UNVERIFIED} <= set(after.lint_flags)
    assert without_evidence(after) == after  # nothing left to remove
