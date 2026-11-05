"""Structured-memory extraction helpers.

This module turns one already-selected personal-memory batch into validated
``memory_items`` rows. Response-time projection and privacy policy live separately in
``structured_memory_context``.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from hina_bot.ai.memory_visual import MEMORY_VISUAL_CONFIDENCE_CAP
from hina_bot.ai.prompts import load_prompt
from hina_bot.core.memory_annotations import (
    EVIDENCE_MAX_CHARS,
    LINT_DEFAULTED,
    LINT_DIRECTIVE_REJECTED,
    LINT_DIRECTIVE_UNVERIFIED,
    LINT_DISCLOSURE_DEFAULTED,
    LINT_EVIDENCE_MISMATCH,
    LINT_INVALID_EVENT_AT,
    LINT_INVALID_RECURRENCE,
    LINT_KIND_DEFAULTED,
    LINT_PAST_MARKER,
    LINT_RECURRENCE_MISSING,
    LINT_SOURCE_UNAVAILABLE,
    LINT_UNDATED_EVENT,
    LINT_VISUAL_GROUNDED,
    SLOT_MAX_CHARS,
    Annotator,
    Audience,
    DirectiveType,
    MemoryAnnotation,
    Stability,
    TimeScope,
    UsageMode,
    clean_keywords,
    enforce_consistency,
    evidence_matches,
    has_past_marker,
    legacy_annotation_defaults,
    local_turn_time,
    valid_event_at,
    valid_recurrence,
)
from hina_bot.core.memory_context import decode_memory_context, lookup_derived
from hina_bot.core.memory_items import (
    MEMORY_CONTENT_MAX_CHARS,
    MemoryDisclosure,
    MemoryKind,
    RelationshipEvidence,
)

_MAX_ITEMS_PER_BATCH = 24
_MAX_CONTENT_CHARS = MEMORY_CONTENT_MAX_CHARS
_RECONCILIATION_RELATIONS = frozenset({"duplicate", "corrects", "conflicts"})
AUTO_RECONCILIATION_MIN_CONFIDENCE = 0.95
# An explicit correction of the same single-valued attribute needs less certainty (#449 §5).
SAME_SLOT_CORRECTION_MIN_CONFIDENCE = 0.8
ANNOTATOR_VERSION = "extraction-v2"
# Decision 18: a relationship observation adds nothing when a recent same-space one covers it.
RELATIONSHIP_SUPPRESSION_WINDOW = timedelta(days=14)
_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)

# Field rules shared by extraction and the offline annotation backfill (#451).
ANNOTATION_FIELD_RULES = load_prompt("annotation_fields.md", section="memory")

SHADOW_EXTRACTION_POLICY = load_prompt("extraction.md", section="memory").replace(
    "{{ANNOTATION_FIELD_RULES}}", ANNOTATION_FIELD_RULES,
)

DIRECTIVE_VERIFICATION_POLICY = load_prompt("directive_verification.md", section="memory")


@dataclass(frozen=True)
class MemoryRelationProposal:
    relation: str
    target_item_id: int
    confidence: float


@dataclass(frozen=True)
class ExtractedMemoryItem:
    content: str
    kind: MemoryKind
    disclosure: MemoryDisclosure
    confidence: float
    source_message_ids: tuple[str, ...]
    relationship_evidence: RelationshipEvidence = field(default_factory=RelationshipEvidence)
    relation: MemoryRelationProposal | None = None
    # Non-relationship items carry a draft annotation; its item id is filled at persist time.
    annotation: MemoryAnnotation | None = None


@dataclass(frozen=True)
class ExtractionParseResult:
    items: tuple[ExtractedMemoryItem, ...]
    rejected_items: int = 0
    rejected_relations: int = 0
    rejected_relationship_evidence: int = 0
    valid: bool = True


@dataclass(frozen=True)
class ShadowPersistResult:
    stored: int
    duplicates: int
    item_ids: tuple[int | None, ...]
    annotated: int = 0
    reconfirmed: int = 0
    suppressed_relationships: int = 0


@dataclass(frozen=True)
class ReconciliationPersistResult:
    stored: int
    applied: int
    deferred: int
    reconfirmed: int = 0
    reinforced: int = 0


def _row_value(row, key: str, default=""):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def build_shadow_turns(
    pending,
    *,
    include_replies: bool,
    zone_name: str | None = None,
) -> tuple[list[dict], set[str], int, dict[str, bool]]:
    """Build extractor input from one exact structured-memory pending batch.

    With ``zone_name`` each turn also carries its local time and weekday so relative
    dates ("다음 주 화요일") can be resolved into absolute ``event_at`` values.
    """

    turns: list[dict] = []
    source_ids: set[str] = set()
    source_public_at_capture: dict[str, bool] = {}
    context_items = 0
    for turn in pending:
        message_id = str(_row_value(turn, "message_id", "")).strip()
        if not message_id:
            # Production rows always have message ids. Refuse provenance-less shadow rows rather
            # than inventing an identifier when a test/legacy adapter does not provide one.
            continue
        context = decode_memory_context(_row_value(turn, "memory_context"))
        # The retained ``exportable`` column now stores per-turn public-at-capture visibility.
        # Older rows may still be conservatively false from the legacy summary era, which remains safe.
        public_at_capture = bool(_row_value(turn, "exportable", False))
        local = local_turn_time(_row_value(turn, "created_at"), zone_name) if zone_name else ("", "")
        item = {
            "message_id": message_id,
            "at": _row_value(turn, "created_at"),
            **({"at_local": local[0], "weekday": local[1]} if local[0] else {}),
            "user": _row_value(turn, "content"),
            "public_at_capture": public_at_capture,
            **({"hina": _row_value(turn, "reply")}
               if include_replies and not lookup_derived(turn) else {}),
            **({"context": context} if context else {}),
        }
        turns.append(item)
        source_ids.add(message_id)
        source_public_at_capture[message_id] = public_at_capture
        context_items += len(context)
    return turns, source_ids, context_items, source_public_at_capture


def shadow_extraction_payload(
    *,
    speaker_id: str,
    speaker_name: str,
    realm: str,
    channel_id: str,
    turns: list[dict],
    evidence_context: list[dict] | None = None,
    candidates: list[dict] | None = None,
) -> dict:
    """The extractor input shared by the runtime and the offline extraction eval."""

    return {
        "speaker_id": str(speaker_id),
        "speaker_name": speaker_name,
        "origin": {"realm": realm, "channel_id": str(channel_id)},
        "turns": turns,
        **({"recent_evidence_context": evidence_context} if evidence_context else {}),
        **({"existing_memory_candidates": candidates} if candidates else {}),
    }


def build_shadow_evidence_context(rows) -> tuple[list[dict], int]:
    """Serialize previous turns as read-only continuity evidence without source ids."""

    evidence: list[dict] = []
    context_items = 0
    for turn in rows:
        context = decode_memory_context(_row_value(turn, "memory_context"))
        item = {
            "at": _row_value(turn, "created_at"),
            "user": _row_value(turn, "content"),
            "hina": "" if lookup_derived(turn) else _row_value(turn, "reply"),
            **({"context": context} if context else {}),
        }
        evidence.append(item)
        context_items += len(context)
    return evidence, context_items


def _json_text(text: str) -> str:
    stripped = text.strip()
    match = _CODE_FENCE.fullmatch(stripped)
    return match.group(1).strip() if match else stripped


def _envelope_rows(root, key: str) -> list | None:
    """Rows from ``{key: [...]}``, or from a bare array some models return instead.

    Only the envelope is relaxed; callers still validate every row.
    """

    if isinstance(root, list):
        return root
    rows = root.get(key) if isinstance(root, dict) else None
    return rows if isinstance(rows, list) else None


def _parse_relationship_evidence(raw, kind: MemoryKind) -> tuple[RelationshipEvidence, int]:
    value = raw.get("relationship_evidence")
    if value is None:
        return RelationshipEvidence(), 0
    if kind != MemoryKind.RELATIONSHIP or not isinstance(value, dict):
        return RelationshipEvidence(), 1

    accepted: dict[str, int] = {}
    rejected = 0
    allowed = set(RelationshipEvidence.__dataclass_fields__)
    for axis, level in value.items():
        if (
            axis not in allowed
            or isinstance(level, bool)
            or not isinstance(level, int)
            or not 1 <= level <= 4
        ):
            rejected += 1
            continue
        accepted[axis] = level
    return RelationshipEvidence.from_mapping(accepted), rejected


def _enum_or_none(enum, value):
    try:
        return enum(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _legacy_item_fields(raw: dict) -> tuple[MemoryKind | None, MemoryDisclosure, tuple[str, ...]]:
    """Read kind/disclosure without dropping an otherwise valid item over a bad label.

    Models sometimes copy usage values into these legacy fields ("disclosure":
    "sensitive"). Decision 15 makes only relationship-or-not meaningful, so an unknown
    kind becomes ``fact`` unless relationship evidence makes it ambiguous (then None:
    reject). An unknown disclosure becomes the narrowest ``local``.
    """

    flags: list[str] = []
    kind = _enum_or_none(MemoryKind, raw.get("kind"))
    if kind is None:
        if raw.get("relationship_evidence"):
            return None, MemoryDisclosure.LOCAL, ()
        kind = MemoryKind.FACT
        flags.append(LINT_KIND_DEFAULTED)
    disclosure = _enum_or_none(MemoryDisclosure, raw.get("disclosure"))
    if disclosure is None:
        disclosure = MemoryDisclosure.LOCAL
        flags.append(LINT_DISCLOSURE_DEFAULTED)
    return kind, disclosure, tuple(flags)


def parse_annotation(raw: dict, kind: MemoryKind, cited: list[dict]) -> MemoryAnnotation:
    """Normalize one item's judgments; never widens usage beyond what validation supports.

    ``cited`` holds the source turns ({"user", "at"}) the item quotes. Missing or invalid
    judgments fall back to the quieter reading (principle 6) and are recorded as lint flags.
    """

    flags: list[str] = []
    default_usage, default_directive = legacy_annotation_defaults(kind.value)
    usage = _enum_or_none(UsageMode, raw.get("usage_mode"))
    directive_type = _enum_or_none(DirectiveType, raw.get("directive_type"))
    if usage is None:
        usage, directive_type = default_usage, default_directive
        flags.append(LINT_DEFAULTED)
    time_scope = _enum_or_none(TimeScope, raw.get("time_scope"))
    if time_scope is None:
        time_scope = TimeScope.UNKNOWN
        flags.append(LINT_DEFAULTED)
    stability = _enum_or_none(Stability, raw.get("stability"))
    # An empty string means the model omitted the field, not that it is malformed.
    event_at = str(raw.get("event_at") or "").strip() or None
    if event_at is not None and not valid_event_at(event_at):
        event_at = None
        flags.append(LINT_INVALID_EVENT_AT)
    if time_scope in {TimeScope.PLANNED, TimeScope.EPISODIC} and not event_at:
        # P2 applies the default expiry (observation + 30 days) to these.
        flags.append(LINT_UNDATED_EVENT)
    recurrence = str(raw.get("recurrence") or "").strip() or None
    if recurrence is not None and not valid_recurrence(recurrence):
        recurrence = None
        flags.append(LINT_INVALID_RECURRENCE)
    if time_scope == TimeScope.RECURRING and not recurrence:
        # P2 cannot compute the next occurrence; surface it for review.
        flags.append(LINT_RECURRENCE_MISSING)
    audience = _enum_or_none(Audience, raw.get("audience")) or Audience.OWNER
    importance = raw.get("importance")
    if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 3:
        importance = 2
    slot = raw.get("slot") if isinstance(raw.get("slot"), str) else ""
    explicit = raw.get("explicit") is True

    utterances = [str(turn.get("user", "")) for turn in cited]
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), str) else ""
    evidence = evidence.strip()[:EVIDENCE_MAX_CHARS]
    if not evidence_matches(evidence, utterances):
        # Unquoted judgments cannot justify proactive use. Directives are not demoted here:
        # boundaries must persist (principle 6) and the verification pass checks them.
        evidence = ""
        # Backfilled items whose source turns are gone had nothing to quote.
        flags.append(LINT_EVIDENCE_MISMATCH if utterances else LINT_SOURCE_UNAVAILABLE)
        if usage == UsageMode.AMBIENT:
            usage = UsageMode.CONTEXTUAL
    elif time_scope == TimeScope.ONGOING and has_past_marker(evidence):
        # A past/completed cue contradicts "still true". Flag for review rather than
        # rewriting to past ("예전에 말했듯이..." is a known false positive).
        flags.append(LINT_PAST_MARKER)
        if usage == UsageMode.AMBIENT:
            usage = UsageMode.CONTEXTUAL
    observed_at = max((str(turn.get("at", "")) for turn in cited), default="")
    annotation = MemoryAnnotation(
        memory_item_id=0,
        item_revision=0,
        time_scope=time_scope,
        stability=stability,
        observed_at=observed_at,
        event_at=event_at,
        recurrence=recurrence,
        usage_mode=usage,
        directive_type=directive_type,
        audience=audience,
        importance=importance,
        slot=slot.strip()[:SLOT_MAX_CHARS],
        keywords=clean_keywords(raw.get("keywords")),
        evidence=evidence,
        explicit=explicit,
        lint_flags=tuple(dict.fromkeys(flags)),
        last_confirmed_at=observed_at,
        annotator=Annotator.EXTRACTOR,
        annotator_version=ANNOTATOR_VERSION,
    )
    # Origin visibility is not known until persist time; assume public here and
    # narrow the audience again once the item's capture visibility is fixed.
    return enforce_consistency(annotation, public_origin=True)


def _cites_visual(refs, source_ids: tuple[str, ...], visual_message_ids) -> bool:
    """Whether an item names an image that this request attached to one of its sources."""

    if not visual_message_ids or not isinstance(refs, list):
        return False
    cited = {value.strip() for value in refs if isinstance(value, str)}
    return bool(cited & set(visual_message_ids) & set(source_ids))


def parse_shadow_extraction(
    text: str,
    *,
    allowed_source_ids: set[str],
    allowed_target_item_ids: set[int] | None = None,
    source_turns: dict[str, dict] | None = None,
    visual_message_ids: frozenset[str] | set[str] = frozenset(),
) -> ExtractionParseResult:
    """Validate model output without repairing or widening its provenance.

    ``source_turns`` maps message ids to their turn ({"user", "at"}); with it, every
    non-relationship item gets a validated annotation draft.

    ``visual_message_ids`` names the turns that had an image in this request. Only those
    can back a ``visual_refs`` claim (#472): such an item is capped in confidence, marked
    ``visual_grounded``, and rejected unless a cited user utterance contains its evidence,
    because a photo alone never justifies a memory.
    """

    try:
        root = json.loads(_json_text(text))
    except (json.JSONDecodeError, TypeError):
        return ExtractionParseResult((), rejected_items=1, valid=False)
    rows = _envelope_rows(root, "items")
    if rows is None:
        return ExtractionParseResult((), rejected_items=1, valid=False)

    allowed_targets = allowed_target_item_ids or set()
    accepted: list[ExtractedMemoryItem] = []
    rejected = 0
    rejected_relations = 0
    rejected_relationship_evidence = 0
    for raw in rows[:_MAX_ITEMS_PER_BATCH]:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        try:
            content = raw["content"].strip()
            confidence = float(raw["confidence"])
            source_values = raw["source_message_ids"]
        except (KeyError, AttributeError, TypeError, ValueError):
            rejected += 1
            continue
        kind, disclosure, defaulted_flags = _legacy_item_fields(raw)
        if kind is None:
            rejected += 1
            continue
        valid_scalar_fields = (
            content
            and len(content) <= _MAX_CONTENT_CHARS
            and math.isfinite(confidence)
            and 0 <= confidence <= 1
            and isinstance(source_values, list)
        )
        if not valid_scalar_fields:
            rejected += 1
            continue
        source_ids = tuple(dict.fromkeys(str(value).strip() for value in source_values))
        valid_sources = (
            source_ids
            and all(source_ids)
            and all(value in allowed_source_ids for value in source_ids)
        )
        if not valid_sources:
            rejected += 1
            continue
        visual_grounded = kind != MemoryKind.RELATIONSHIP and _cites_visual(
            raw.get("visual_refs"), source_ids, visual_message_ids
        )
        if visual_grounded:
            if source_turns is not None:
                quote = raw.get("evidence") if isinstance(raw.get("evidence"), str) else ""
                utterances = [
                    str(source_turns[value].get("user", ""))
                    for value in source_ids if value in source_turns
                ]
                if not evidence_matches(quote.strip()[:EVIDENCE_MAX_CHARS], utterances):
                    rejected += 1
                    continue
            confidence = min(confidence, MEMORY_VISUAL_CONFIDENCE_CAP)
        relationship_evidence, rejected_evidence = _parse_relationship_evidence(raw, kind)
        rejected_relationship_evidence += rejected_evidence
        relation = None
        raw_relation = raw.get("relation")
        if raw_relation is not None:
            try:
                relation_type = str(raw_relation["type"]).strip()
                target_item_id = int(raw_relation["target_item_id"])
                relation_confidence = float(raw_relation["confidence"])
                valid_relation = (
                    relation_type in _RECONCILIATION_RELATIONS
                    and target_item_id in allowed_targets
                    and math.isfinite(relation_confidence)
                    and 0 <= relation_confidence <= 1
                )
            except (KeyError, TypeError, ValueError):
                valid_relation = False
            if valid_relation:
                relation = MemoryRelationProposal(
                    relation=relation_type,
                    target_item_id=target_item_id,
                    confidence=relation_confidence,
                )
            else:
                rejected_relations += 1
        annotation = None
        if source_turns is not None and kind != MemoryKind.RELATIONSHIP:
            cited = [source_turns[value] for value in source_ids if value in source_turns]
            annotation = parse_annotation(raw, kind, cited)
            if defaulted_flags:
                annotation = replace(annotation, lint_flags=tuple(
                    dict.fromkeys((*annotation.lint_flags, *defaulted_flags))
                ))
            if visual_grounded:
                annotation = replace(annotation, lint_flags=tuple(
                    dict.fromkeys((*annotation.lint_flags, LINT_VISUAL_GROUNDED))
                ))
        accepted.append(ExtractedMemoryItem(
            content=content,
            kind=kind,
            disclosure=disclosure,
            confidence=confidence,
            source_message_ids=source_ids,
            relationship_evidence=relationship_evidence,
            relation=relation,
            annotation=annotation,
        ))
    rejected += max(0, len(rows) - _MAX_ITEMS_PER_BATCH)
    return ExtractionParseResult(
        tuple(accepted),
        rejected_items=rejected,
        rejected_relations=rejected_relations,
        rejected_relationship_evidence=rejected_relationship_evidence,
    )


def directive_verification_payload(
    items: tuple[ExtractedMemoryItem, ...], source_turns: dict[str, dict]
) -> dict:
    """Serialize only the directive items and the user utterances they cite."""

    entries = []
    for index, item in enumerate(items):
        annotation = item.annotation
        if annotation is None or annotation.usage_mode != UsageMode.DIRECTIVE:
            continue
        entries.append({
            "index": index,
            "content": item.content,
            "directive_type": annotation.directive_type.value if annotation.directive_type else "",
            "evidence": annotation.evidence,
            "source_turns": [
                {"user": str(source_turns[value].get("user", "")),
                 **({"at_local": source_turns[value]["at_local"]}
                    if source_turns[value].get("at_local") else {})}
                for value in item.source_message_ids if value in source_turns
            ],
        })
    return {"items": entries} if entries else {}


def apply_directive_verification(
    items: tuple[ExtractedMemoryItem, ...], text: str | None
) -> tuple[tuple[ExtractedMemoryItem, ...], int, int]:
    """Apply verifier verdicts; returns (items, verified, demoted).

    Without a usable verdict a directive stays a directive but unverified: it still
    applies in its own space, but is not carried across spaces (#449 §7.2).
    """

    verdicts: dict[int, str] = {}
    if text:
        try:
            root = json.loads(_json_text(text))
            for row in _envelope_rows(root, "results") or []:
                if isinstance(row, dict) and isinstance(row.get("index"), int):
                    verdicts[row["index"]] = str(row.get("verdict", ""))
        except (json.JSONDecodeError, TypeError, AttributeError):
            verdicts = {}
    updated = []
    verified = demoted = 0
    for index, item in enumerate(items):
        annotation = item.annotation
        if annotation is None or annotation.usage_mode != UsageMode.DIRECTIVE:
            updated.append(item)
            continue
        verdict = verdicts.get(index)
        if verdict == "temporary" and annotation.directive_type == DirectiveType.STYLE:
            verdict = "reject"
        if verdict == "keep":
            annotation = replace(annotation, verified=True)
            verified += 1
        elif verdict == "temporary":
            annotation = replace(
                annotation, verified=True, time_scope=TimeScope.ONGOING, stability=Stability.SHORT,
            )
            verified += 1
        elif verdict == "reject":
            annotation = replace(
                annotation,
                usage_mode=UsageMode.CONTEXTUAL,
                directive_type=None,
                verified=False,
                lint_flags=tuple(dict.fromkeys((*annotation.lint_flags, LINT_DIRECTIVE_REJECTED))),
            )
            demoted += 1
        else:
            annotation = replace(
                annotation,
                lint_flags=tuple(dict.fromkeys((*annotation.lint_flags, LINT_DIRECTIVE_UNVERIFIED))),
            )
        updated.append(replace(item, annotation=annotation))
    return tuple(updated), verified, demoted


def _reconciliation_grams(text: str) -> set[str]:
    """Lightweight ranking signal, never an authorization or duplicate gate."""

    words = re.findall(r"[가-힣a-z0-9]+", text.lower())
    return {word[index:index + 2] for word in words for index in range(len(word) - 1)}


def select_reconciliation_candidates(items, turns, *, limit: int = 24):
    """Reserve room for factual and relationship items; rank within each by overlap.

    Lexical overlap is only a hint for bounded LLM context. No item is rejected
    or declared duplicate based on this score.
    """

    candidates = list(items)
    if len(candidates) <= limit:
        return candidates
    query_grams = _reconciliation_grams(
        " ".join(str(turn.get("user", "")) for turn in turns)
    )

    def score(item):
        overlap = len(_reconciliation_grams(item.content) & query_grams)
        return (overlap, item.id)

    factual = sorted(
        (item for item in candidates if item.kind != MemoryKind.RELATIONSHIP),
        key=score,
        reverse=True,
    )
    relationship = sorted(
        (item for item in candidates if item.kind == MemoryKind.RELATIONSHIP),
        key=score,
        reverse=True,
    )
    half = limit // 2
    selected = factual[:half] + relationship[:limit - half]
    remainder = factual[half:] + relationship[limit - half:]
    selected.extend(sorted(remainder, key=score, reverse=True)[:limit - len(selected)])
    return sorted(selected, key=lambda item: item.id)


def build_reconciliation_candidates(items, annotations=None) -> list[dict]:
    """Serialize bounded same-space candidates without exposing extra provenance.

    Annotated candidates also show when they apply, so a new observation can be
    compared against an old plan or a past state rather than only its wording.
    """

    annotations = annotations or {}
    candidates = []
    for item in items:
        entry = {
            "id": item.id,
            "content": item.content,
            "kind": item.kind.value,
            "disclosure": item.disclosure.value,
            "confidence": item.confidence,
        }
        annotation = annotations.get(item.id)
        if annotation is not None:
            entry["time_scope"] = annotation.time_scope.value
            if annotation.event_at:
                entry["event_at"] = annotation.event_at
            if annotation.slot:
                entry["slot"] = annotation.slot
            entry["observed"] = annotation.observed_at[:10]
        candidates.append(entry)
    return candidates


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T")).replace(tzinfo=None)
    except ValueError:
        return None


def _covered_relationship(item, origin_public: bool, channel_id: str, existing, observed_at) -> bool:
    """Decision 18: a recent same-space observation already shows every axis at least as high."""

    observed = _parse_time(observed_at)
    if observed is None:
        return False
    new_axes = item.relationship_evidence.as_dict()
    for entry in existing:
        if (
            entry.kind != MemoryKind.RELATIONSHIP
            or entry.status.value != "active"
            or entry.origin_channel_id != channel_id
            or entry.origin_public_at_capture != origin_public
        ):
            continue
        created = _parse_time(entry.created_at)
        if created is None or not timedelta(0) <= observed - created <= RELATIONSHIP_SUPPRESSION_WINDOW:
            continue
        old_axes = entry.relationship_evidence.as_dict()
        if all(level <= old_axes.get(axis, 0) for axis, level in new_axes.items()):
            return True
    return False


def persist_shadow_items_detailed(
    store,
    scope,
    items: tuple[ExtractedMemoryItem, ...],
    *,
    source_public_at_capture: dict[str, bool] | None = None,
    batch_observed_at: str = "",
) -> ShadowPersistResult:
    """Append validated items and return ids aligned with the parsed item order.

    Annotations are written in the caller's transaction next to their items. A
    same-space re-mention of an active item is counted as a confirmation instead.
    """

    existing = store.memory_items(
        scope.user_id,
        origin_realm=scope.realm,
        include_superseded=True,
    )
    signatures = {
        (
            item.content,
            item.kind,
            item.disclosure,
            item.source_message_ids,
            item.origin_channel_id,
        ): item.id
        for item in existing
    }
    # Unlike retry signatures (which include source ids), this suppresses
    # repeated canonical content from later batches in the very same scope.
    # Different evidence is a distinct relationship observation.
    active_signatures = {
        (
            entry.content, entry.kind, entry.disclosure,
            entry.origin_channel_id, entry.origin_public_at_capture,
            tuple(sorted(entry.relationship_evidence.as_dict().items())),
        ): entry.id
        for entry in existing if entry.status.value == "active"
    }
    annotate = getattr(store, "upsert_memory_annotation", None)
    reconfirm = getattr(store, "reconfirm_memory_annotation", None)
    stored = 0
    duplicates = 0
    annotated = 0
    reconfirmed = 0
    suppressed = 0
    item_ids: list[int | None] = []
    channel_id = str(scope.channel_id)
    for item in items:
        signature = (
            item.content,
            item.kind,
            item.disclosure,
            item.source_message_ids,
            channel_id,
        )
        if signature in signatures:
            duplicates += 1
            item_ids.append(signatures[signature])
            continue
        if source_public_at_capture is None:
            origin_public_at_capture = bool(scope.public_at_capture)
        else:
            origin_public_at_capture = bool(
                scope.guild_id is not None
                and all(source_public_at_capture.get(source_id, False)
                        for source_id in item.source_message_ids)
            )
        content_signature = (
            item.content, item.kind, item.disclosure,
            channel_id, origin_public_at_capture,
            tuple(sorted(item.relationship_evidence.as_dict().items())),
        )
        if content_signature in active_signatures:
            duplicates += 1
            item_ids.append(None)
            if callable(reconfirm) and item.annotation is not None and reconfirm(
                active_signatures[content_signature], item.annotation.observed_at
            ):
                reconfirmed += 1
            continue
        if item.kind == MemoryKind.RELATIONSHIP and _covered_relationship(
            item, origin_public_at_capture, channel_id, existing, batch_observed_at
        ):
            suppressed += 1
            item_ids.append(None)
            continue
        item_scope = replace(scope, public_at_capture=origin_public_at_capture)
        item_id = store.add_memory_item(
            item_scope,
            item.content,
            kind=item.kind,
            disclosure=item.disclosure,
            source_message_ids=item.source_message_ids,
            confidence=item.confidence,
            relationship_evidence=item.relationship_evidence,
        )
        if callable(annotate) and item.annotation is not None:
            public_origin = scope.guild_id is not None and origin_public_at_capture
            annotate(enforce_consistency(
                replace(item.annotation, memory_item_id=item_id),
                public_origin=public_origin,
            ))
            annotated += 1
        signatures[signature] = item_id
        active_signatures[content_signature] = item_id
        item_ids.append(item_id)
        stored += 1
    return ShadowPersistResult(
        stored, duplicates, tuple(item_ids),
        annotated=annotated, reconfirmed=reconfirmed, suppressed_relationships=suppressed,
    )


def persist_shadow_items(
    store,
    scope,
    items: tuple[ExtractedMemoryItem, ...],
    *,
    source_public_at_capture: dict[str, bool] | None = None,
) -> tuple[int, int]:
    """Backward-compatible count-only wrapper for shadow item persistence."""

    result = persist_shadow_items_detailed(
        store,
        scope,
        items,
        source_public_at_capture=source_public_at_capture,
    )
    return result.stored, result.duplicates


def _same_slot(store, item: ExtractedMemoryItem) -> bool:
    if item.annotation is None or not item.annotation.slot or item.relation is None:
        return False
    reader = getattr(store, "memory_annotation", None)
    target = reader(item.relation.target_item_id) if callable(reader) else None
    return target is not None and target.slot == item.annotation.slot


def persist_reconciliation_proposals(
    store,
    scope,
    items: tuple[ExtractedMemoryItem, ...],
    item_ids: tuple[int | None, ...],
) -> ReconciliationPersistResult:
    """Persist proposals and conservatively apply safe lifecycle transitions.

    An applied duplicate also confirms its surviving target, so frequently
    re-mentioned memories stay distinguishable from stale one-off ones. When the
    target has no quote yet (a backfilled memory), the duplicate's judgment and quote
    reinforce it first, within this extraction scope's disclosure space (#471).
    """

    stored = 0
    applied = 0
    deferred = 0
    reconfirmed = 0
    reinforced = 0
    finder = getattr(store, "memory_reconciliation_proposal_id", None)
    applier = getattr(store, "apply_memory_reconciliation_proposal", None)
    reconfirm = getattr(store, "reconfirm_memory_annotation", None)
    reinforce = getattr(store, "reinforce_memory_annotation", None)
    for item, item_id in zip(items, item_ids, strict=True):
        if item_id is None or item.relation is None:
            continue
        if item_id == item.relation.target_item_id:
            continue
        proposal_id = store.add_memory_reconciliation_proposal(
            scope,
            new_memory_item_id=item_id,
            target_memory_item_id=item.relation.target_item_id,
            relation=item.relation.relation,
            confidence=item.relation.confidence,
            source_message_ids=item.source_message_ids,
        )
        if proposal_id is not None:
            stored += 1
        elif callable(finder):
            proposal_id = finder(
                new_memory_item_id=item_id,
                target_memory_item_id=item.relation.target_item_id,
                relation=item.relation.relation,
            )
        if proposal_id is None or not callable(applier):
            continue
        threshold = AUTO_RECONCILIATION_MIN_CONFIDENCE
        if item.relation.relation == "corrects" and _same_slot(store, item):
            threshold = SAME_SLOT_CORRECTION_MIN_CONFIDENCE
        outcome = applier(proposal_id, min_confidence=threshold)
        if outcome == "applied":
            applied += 1
            if item.relation.relation == "duplicate" and item.annotation is not None:
                target_id = item.relation.target_item_id
                if callable(reinforce) and reinforce(
                    scope, target_item_id=target_id, evidence_item_id=item_id
                ):
                    reinforced += 1
                if callable(reconfirm) and reconfirm(target_id, item.annotation.observed_at):
                    reconfirmed += 1
        elif outcome != "already_applied":
            deferred += 1
    return ReconciliationPersistResult(
        stored, applied, deferred, reconfirmed=reconfirmed, reinforced=reinforced
    )


__all__ = [
    "ANNOTATION_FIELD_RULES",
    "ANNOTATOR_VERSION",
    "AUTO_RECONCILIATION_MIN_CONFIDENCE",
    "DIRECTIVE_VERIFICATION_POLICY",
    "RELATIONSHIP_SUPPRESSION_WINDOW",
    "SAME_SLOT_CORRECTION_MIN_CONFIDENCE",
    "SHADOW_EXTRACTION_POLICY",
    "ExtractedMemoryItem",
    "ExtractionParseResult",
    "MemoryRelationProposal",
    "ReconciliationPersistResult",
    "ShadowPersistResult",
    "apply_directive_verification",
    "build_reconciliation_candidates",
    "build_shadow_evidence_context",
    "build_shadow_turns",
    "directive_verification_payload",
    "parse_annotation",
    "parse_shadow_extraction",
    "persist_reconciliation_proposals",
    "persist_shadow_items",
    "persist_shadow_items_detailed",
    "select_reconciliation_candidates",
    "shadow_extraction_payload",
]
