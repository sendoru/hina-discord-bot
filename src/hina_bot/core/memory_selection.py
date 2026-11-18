"""Memory v2 tiered selection (#449 §7, #457): which owner memories reach a response.

Pure and deterministic: no model calls and no I/O. Permission comes first (principle 1):
every candidate passes ``memory_access`` or the cross-space boundary rule (§7.2) before
any scoring, and nothing here widens access. Selecting nothing is normal (principle 5).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from .memory_annotations import (
    PROACTIVE_COUNT_MAX,
    UNDATED_EVENT_EXPIRY,
    Annotator,
    MemoryAnnotation,
    Stability,
    TimeScope,
    UsageMode,
    legacy_annotation_defaults,
    normalize_text,
    valid_recurrence,
)
from .memory_items import (
    MemoryAccess,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    memory_access,
)
from .routing import Scope

# Freshness windows for ongoing memories, measured from the last confirmation (§7 2).
STABILITY_WINDOWS: dict[Stability, timedelta | None] = {
    Stability.SHORT: timedelta(days=30),
    Stability.MEDIUM: timedelta(days=120),
    Stability.LONG: timedelta(days=365),
    Stability.PERMANENT: None,
}
JUST_PASSED_WINDOW = timedelta(days=14)
RECENT_EVENT_WINDOW = timedelta(days=14)
DUPLICATE_SIMILARITY = 0.5


class EffectiveStatus(StrEnum):
    CURRENT = "current"
    UNVERIFIED = "unverified"
    UPCOMING = "upcoming"
    JUST_PASSED = "just_passed"
    PAST = "past"
    RECENT_EVENT = "recent_event"
    PAST_EVENT = "past_event"


@dataclass(frozen=True)
class StatusView:
    status: EffectiveStatus
    date: str | None = None  # event date or next occurrence (local)
    noted: str | None = None  # observation/confirmation date for ongoing-style items
    expired: bool = False


@dataclass(frozen=True)
class SelectionContext:
    scope: Scope
    message: str
    now: datetime
    reply_texts: tuple[str, ...] = ()
    hours_since_last_turn: float | None = None
    session_followup: str = "dm"
    explicit_call: bool = True
    task_register: bool = False
    authorized_item_ids: frozenset[int] = frozenset()
    # The user's own recent messages to Hina in this conversation, oldest first (carry).
    recent_own_texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectedMemory:
    item: MemoryItem
    annotation: MemoryAnnotation
    tier: str
    placement: str
    status: StatusView
    use: str
    # What admitted a T2 note: "current" message, reply "chain" or a "carry"-over topic.
    via: str = ""


@dataclass
class MemorySelection:
    selected: list[SelectedMemory] = field(default_factory=list)
    excluded: Counter = field(default_factory=Counter)
    followup_attempted: bool = False

    def by_placement(self, placement: str) -> list[SelectedMemory]:
        return [entry for entry in self.selected if entry.placement == placement]

    def tier_counts(self) -> dict[str, int]:
        return dict(Counter(entry.tier for entry in self.selected))


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace(" ", "T", 1))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _event_bounds(value: str) -> tuple[date, date] | None:
    try:
        parts = [int(part) for part in value.split("-")]
        if len(parts) == 3:
            day = date(*parts)
            return day, day
        first = date(parts[0], parts[1], 1)
    except (ValueError, TypeError, IndexError):
        return None
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return first, following - timedelta(days=1)


_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _next_occurrence(recurrence: str, now: datetime) -> date | None:
    if not valid_recurrence(recurrence):
        return None
    today = now.date()
    if recurrence.startswith("monthly:"):
        day = int(recurrence.removeprefix("monthly:"))
        for offset in range(3):
            month_index = today.month - 1 + offset
            year, month = today.year + month_index // 12, month_index % 12 + 1
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if candidate >= today:
                return candidate
        return None
    days = recurrence.removeprefix("weekly:").split(" ")[0].split(",")
    wanted = {_WEEKDAYS.index(day) for day in days}
    for offset in range(7):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() in wanted:
            return candidate
    return None


def effective_status(annotation: MemoryAnnotation, now: datetime) -> StatusView:
    """Compute the time state from the annotation and the current time (principle 4)."""

    observed = _parse_time(annotation.observed_at)
    confirmed = _parse_time(annotation.last_confirmed_at) or observed
    noted = (confirmed or observed).astimezone(now.tzinfo).date().isoformat() if (
        confirmed or observed
    ) else None
    scope = annotation.time_scope
    if scope == TimeScope.PAST:
        return StatusView(EffectiveStatus.PAST, noted=noted)
    if scope in {TimeScope.PLANNED, TimeScope.EPISODIC}:
        bounds = _event_bounds(annotation.event_at) if annotation.event_at else None
        if bounds is None:
            # Undated plans and events expire 30 days after observation (#451 lint).
            expired = observed is not None and now - observed > UNDATED_EVENT_EXPIRY
            status = EffectiveStatus.UPCOMING if scope == TimeScope.PLANNED else (
                EffectiveStatus.RECENT_EVENT
            )
            return StatusView(status, noted=noted, expired=expired)
        _first, last = bounds
        today = now.date()
        if scope == TimeScope.PLANNED:
            if last >= today:
                return StatusView(EffectiveStatus.UPCOMING, date=annotation.event_at)
            if today - last <= JUST_PASSED_WINDOW:
                return StatusView(EffectiveStatus.JUST_PASSED, date=annotation.event_at)
            return StatusView(EffectiveStatus.PAST, date=annotation.event_at)
        if today - last <= RECENT_EVENT_WINDOW:
            return StatusView(EffectiveStatus.RECENT_EVENT, date=annotation.event_at)
        return StatusView(EffectiveStatus.PAST_EVENT, date=annotation.event_at)
    if scope == TimeScope.RECURRING:
        upcoming = _next_occurrence(annotation.recurrence or "", now)
        if upcoming is not None:
            return StatusView(EffectiveStatus.UPCOMING, date=upcoming.isoformat(), noted=noted)
        return StatusView(EffectiveStatus.CURRENT, noted=noted)
    if scope == TimeScope.ONGOING:
        window = STABILITY_WINDOWS.get(annotation.stability or Stability.MEDIUM)
        if window is not None and confirmed is not None and now - confirmed > window:
            return StatusView(EffectiveStatus.UNVERIFIED, noted=noted)
        return StatusView(EffectiveStatus.CURRENT, noted=noted)
    return StatusView(EffectiveStatus.UNVERIFIED, noted=noted)


def effective_annotation(
    item: MemoryItem, annotation: MemoryAnnotation | None
) -> tuple[MemoryAnnotation, str]:
    """Apply principle 6 to missing or stale annotations; returns (annotation, state)."""

    if annotation is not None and not annotation.is_stale(item.revision):
        return annotation, "current"
    if annotation is not None and annotation.usage_mode == UsageMode.DIRECTIVE:
        # An admin edit made it stale: keep the directive until it is re-annotated.
        return annotation, "stale"
    usage, directive_type = legacy_annotation_defaults(item.kind.value)
    observed = annotation.observed_at if annotation is not None else item.created_at
    fallback = MemoryAnnotation(
        memory_item_id=item.id,
        item_revision=item.revision,
        time_scope=TimeScope.UNKNOWN,
        observed_at=observed,
        last_confirmed_at=observed,
        usage_mode=usage,
        directive_type=directive_type,
        annotator=Annotator.BACKFILL,
        annotator_version="default",
        keywords=annotation.keywords if annotation is not None else (),
        last_used_at=annotation.last_used_at if annotation is not None else None,
    )
    return fallback, "stale" if annotation is not None else "missing"


_TOKEN = re.compile(r"[\w]+", re.UNICODE)
_STOPWORDS = frozenset({
    "히나", "히나야", "히나는", "히나가", "요즘", "오늘", "그냥", "진짜", "너무", "있음", "있어", "했어",
    "사용자", "사용자는", "함", "하는", "중임", "좀", "이거", "그거", "저거", "나는", "내가", "우리",
})


def _tokens(text: str) -> list[str]:
    return [
        token for token in _TOKEN.findall(normalize_text(text))
        if len(token) >= 2 and token not in _STOPWORDS
    ]


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", normalize_text(text))
    return {compact[index:index + 2] for index in range(len(compact) - 1)}


def similar_memory_text(left: str, right: str) -> bool:
    a, b = _bigrams(left), _bigrams(right)
    return bool(a and b) and len(a & b) / len(a | b) >= DUPLICATE_SIMILARITY


def is_light_chat(message: str) -> bool:
    """Greeting, small talk or a bare call: the only session openers that get a follow-up."""

    text = normalize_text(message)
    if len(text) > 40 or "http" in text or "```" in message:
        return False
    return not re.search(
        r"[{}()\[\];=<>]|해\s*줘|봐\s*줘|알려\s*줘|찾아\s*줘|만들어|고쳐|설명|번역|계산|"
        r"코드|에러|오류|어떻게|왜\s|뭐야\?|방법",
        text,
    )


def _scope_relation(item: MemoryItem, scope: Scope) -> str:
    if item.origin_realm == scope.realm and item.origin_channel_id == str(scope.channel_id):
        return "current_channel"
    if scope.guild_id is not None and item.origin_realm == scope.realm:
        return "same_server_other_channel"
    return "cross_space"


def full_memory_items(items, annotations, ctx):
    """Filter the owner's records before choosing a representative for each duplicate."""
    scope = ctx.scope
    visible = []
    for item in items:
        if item.user_id != str(scope.user_id) or item.status != MemoryStatus.ACTIVE:
            continue
        if memory_access(item, scope) == MemoryAccess.HIDDEN:
            continue
        annotation = annotations.get(item.id)
        if scope.guild_id is not None and annotation and annotation.usage_mode == UsageMode.SENSITIVE:
            continue
        if memory_access(item, scope) == MemoryAccess.IMPLICIT and item.kind != MemoryKind.RELATIONSHIP:
            continue
        visible.append(item)

    def priority(item):
        return (
            memory_access(item, scope) == MemoryAccess.FULL,
            item.origin_realm == scope.realm and item.origin_channel_id == str(scope.channel_id),
            item.origin_realm == scope.realm,
            item.updated_at,
            item.id,
        )

    representatives = {}
    for item in visible:
        key = normalize_text(item.content)
        if key not in representatives or priority(item) > priority(representatives[key]):
            representatives[key] = item
    ids = {item.id for item in representatives.values()}
    return [item for item in visible if item.id in ids]


def decision_memory_candidates(items, annotations, ctx) -> list[SelectedMemory]:
    """Expose only full-access records; implicit relationship text stays private."""
    candidates = []
    for item in full_memory_items(items, annotations, ctx):
        if memory_access(item, ctx.scope) != MemoryAccess.FULL:
            continue
        annotation, _ = effective_annotation(item, annotations.get(item.id))
        candidates.append(SelectedMemory(
            item, annotation, "T2", "notes", effective_status(annotation, ctx.now), "if_relevant",
        ))
    return candidates


def selection_provenance(selection: MemorySelection) -> dict:
    """Content-free record of what was chosen and why others were not."""

    return {
        "items": [
            {"store": "personal", "item_id": entry.item.id, "tier": entry.tier,
             "placement": entry.placement, "status": entry.status.status.value,
             **({"via": entry.via} if entry.via else {})}
            for entry in selection.selected
        ],
        "tiers": selection.tier_counts(),
        "excluded": dict(selection.excluded),
        "followup_attempted": selection.followup_attempted,
    }


@dataclass(frozen=True)
class MemoryUsage:
    """Usage metadata to store for one selected note after the reply (§7 5, §7.1)."""

    item_id: int
    used: bool
    last_used_at: str | None = None
    proactive_status: str | None = None
    proactive_count: int | None = None
    last_proactive_at: str | None = None


def _mentions(item: MemoryItem, annotation: MemoryAnnotation, text: str) -> bool:
    """Decision 19: a memory counts as used when the text contains one of its keywords."""

    return mentions_memory_content(item.content, annotation.keywords, text)


def mentions_memory_content(content, keywords, text):
    text = normalize_text(text)
    keywords = [normalize_text(word) for word in keywords]
    keywords = [word for word in keywords if len(word) >= 2]
    if keywords:
        return any(word in text for word in keywords)
    # Unannotated legacy items have no keywords: require two content words instead.
    return sum(term in text for term in _tokens(content)) >= 2


def _used_in(entry: SelectedMemory, answer: str) -> bool:
    return _mentions(entry.item, entry.annotation, answer)


def memory_usage(selection: MemorySelection, answer: str, now: datetime) -> list[MemoryUsage]:
    """Cooldown and follow-up bookkeeping for the notes and profile injected into this reply."""

    stamp = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    updates = []
    for entry in selection.selected:
        if entry.placement not in {"notes", "profile"}:
            continue
        used = _used_in(entry, answer)
        if entry.tier != "T1":
            if used:
                updates.append(MemoryUsage(entry.item.id, True, last_used_at=stamp))
            continue
        status = entry.status.status.value
        previous = (
            entry.annotation.proactive_count
            if entry.annotation.proactive_status == status
            else 0
        )
        # Used: this status is exhausted. Unused: one retry is left for the next session.
        count = PROACTIVE_COUNT_MAX if used else min(PROACTIVE_COUNT_MAX, previous + 1)
        updates.append(MemoryUsage(
            entry.item.id, used,
            last_used_at=stamp if used else None,
            proactive_status=status, proactive_count=count, last_proactive_at=stamp,
        ))
    return updates


@dataclass(frozen=True)
class MemoryReadRun:
    """One turn's tiered selection, handed to the post-reply bookkeeping.

    Active modes record usage for injected notes. Observation modes measure coverage
    against the legacy projection.
    """

    mode: str
    selection: MemorySelection
    message: str = ""
    now: datetime | None = None
    legacy_notes: tuple[tuple[MemoryItem, MemoryAnnotation], ...] = ()
    visual_turn: bool = False
    recent_own_turn: bool = False


def legacy_notes(
    items, annotations: dict[int, MemoryAnnotation], ctx: SelectionContext
) -> tuple[tuple[MemoryItem, MemoryAnnotation], ...]:
    """The same raw records full can expose, for Decision shadow coverage."""
    return tuple(
        (item, effective_annotation(item, annotations.get(item.id))[0])
        for item in full_memory_items(items, annotations, ctx)
        if memory_access(item, ctx.scope) == MemoryAccess.FULL
    )


_ACTIVE_STATUSES = frozenset({
    EffectiveStatus.CURRENT, EffectiveStatus.UPCOMING,
    EffectiveStatus.JUST_PASSED, EffectiveStatus.RECENT_EVENT,
})


@dataclass(frozen=True)
class ShadowCoverage:
    """How many notes a legacy answer used, and how many of them tiered would have sent.

    ``missed_unprompted`` counts misses whose keywords were not in the user's message:
    the answer brought them up from memory alone. ``missed_current`` counts misses
    that are still valid (current, upcoming, just passed or a recent event).
    """

    notes: int
    used: int
    covered: int
    missed_item_ids: tuple[int, ...] = ()
    missed_unprompted: int = 0
    missed_current: int = 0
    missed_usage: dict[str, int] = field(default_factory=dict)
    missed_importance: dict[str, int] = field(default_factory=dict)
    # Used notes covered only through the core profile or a carried-over topic: without
    # them these would have been misses too (ignoring their effect on the T2 cap).
    covered_profile: int = 0
    covered_carry: int = 0

    @property
    def missed(self) -> int:
        return len(self.missed_item_ids)

    def as_dict(self) -> dict:
        return {
            "notes": self.notes, "used": self.used, "covered": self.covered,
            "missed": self.missed, "missed_unprompted": self.missed_unprompted,
            "missed_current": self.missed_current, "missed_usage": dict(self.missed_usage),
            "missed_importance": dict(self.missed_importance),
            "missed_item_ids": list(self.missed_item_ids),
            "covered_profile": self.covered_profile, "covered_carry": self.covered_carry,
        }


def shadow_coverage(run: MemoryReadRun, answer: str) -> ShadowCoverage:
    """Compare the notes a legacy answer used with the shadow selection (content-free)."""

    selected = {entry.item.id: entry for entry in run.selection.selected}
    used = [(item, annotation) for item, annotation in run.legacy_notes
            if _mentions(item, annotation, answer)]
    covering = [selected[item.id] for item, _annotation in used if item.id in selected]
    missed = [(item, annotation) for item, annotation in used if item.id not in selected]
    now = run.now or datetime.now(UTC)
    return ShadowCoverage(
        notes=len(run.legacy_notes),
        used=len(used),
        covered=len(used) - len(missed),
        missed_item_ids=tuple(item.id for item, _annotation in missed),
        missed_unprompted=sum(
            1 for item, annotation in missed if not _mentions(item, annotation, run.message)
        ),
        missed_current=sum(
            1 for _item, annotation in missed
            if (status := effective_status(annotation, now)).status in _ACTIVE_STATUSES
            and not status.expired
        ),
        missed_usage=dict(Counter(annotation.usage_mode.value for _item, annotation in missed)),
        missed_importance=dict(Counter(
            str(annotation.importance) for _item, annotation in missed
        )),
        covered_profile=sum(1 for entry in covering if entry.tier == "Tp"),
        covered_carry=sum(1 for entry in covering if entry.via == "carry"),
    )


