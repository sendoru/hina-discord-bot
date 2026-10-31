"""Memory v2 tiered selection (#449 §7, #457): which owner memories reach a response.

Pure and deterministic: no model calls and no I/O. Permission comes first (principle 1):
every candidate passes ``memory_access`` or the cross-space boundary rule (§7.2) before
any scoring, and nothing here widens access. Selecting nothing is normal (principle 5).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from .memory_annotations import (
    PROACTIVE_COUNT_MAX,
    UNDATED_EVENT_EXPIRY,
    Annotator,
    Audience,
    DirectiveType,
    MemoryAnnotation,
    Stability,
    TimeScope,
    UsageMode,
    legacy_annotation_defaults,
    normalize_text,
    valid_recurrence,
)
from .memory_items import MemoryAccess, MemoryItem, MemoryKind, MemoryStatus, memory_access
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
T1_UPCOMING_WINDOW = timedelta(days=3)
T1_RECENT_AMBIENT_WINDOW = timedelta(days=14)
SESSION_GAP = timedelta(hours=8)
COOLDOWN = timedelta(hours=72)
T0_MAX_ITEMS = 6
T0_MAX_CHARS = 600
T1_MAX_CANDIDATES = 2
T2_MAX_ITEMS = 4
T2_MAX_CHARS = 900
# Relevance thresholds; one keyword hit scores 1.0, one content-word hit 0.5.
AMBIENT_THRESHOLD = 1.0
EXPLICIT_RELIEF = 0.5
DUPLICATE_SIMILARITY = 0.5
FOLLOWUP_MODES = frozenset({"off", "dm", "all"})
PROACTIVE_OPT_OUT_SLOT = "proactive_followup_off"


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


@dataclass(frozen=True)
class SelectedMemory:
    item: MemoryItem
    annotation: MemoryAnnotation
    tier: str
    placement: str
    status: StatusView
    use: str


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


def _term_match(query: str, term: str) -> bool:
    if query == term:
        return True
    if min(len(query), len(term)) >= 2 and (term in query or query in term):
        return True
    # Korean particles and endings: a shared two-character stem counts ("배고프다"/"배고픔").
    return min(len(query), len(term)) >= 2 and query[:2] == term[:2] and len(term) >= 3


def _keywords(annotation: MemoryAnnotation) -> list[str]:
    return [normalize_text(word) for word in annotation.keywords if len(normalize_text(word)) >= 2]


def query_idf(pool, texts) -> dict[str, float]:
    """Inverse document frequency of each query word over the owner's candidates.

    A word that matches most of the owner's memories ("보리" for a cat owner) says
    little about which one is meant; a rare one ("사료") says a lot. Used for ranking
    only: admission thresholds stay on the unweighted score.
    """

    documents = [
        {*_keywords(annotation), *_tokens(item.content)} for item, annotation in pool
    ]
    idf: dict[str, float] = {}
    for text, _weight in texts:
        for query in _tokens(text):
            if query in idf:
                continue
            frequency = sum(
                1 for terms in documents if any(_term_match(query, term) for term in terms)
            )
            idf[query] = math.log(1 + len(documents) / frequency) if frequency else 0.0
    return idf


def _relevance(
    item: MemoryItem, annotation: MemoryAnnotation, texts, idf: dict[str, float] | None = None
) -> tuple[float, int, float]:
    """Score item terms against weighted texts; returns (score, keyword hits, ranking score).

    ``score`` and the hit count decide admission. The ranking score weighs each hit by
    the IDF of the query word that matched it, when ``idf`` is given.
    """

    keywords = _keywords(annotation)
    content_terms = _tokens(item.content)
    score = 0.0
    ranked = 0.0
    keyword_hits = 0

    def rarity(matched: list[str]) -> float:
        return max(idf.get(query, 0.0) for query in matched) if idf is not None else 1.0

    for text, weight in texts:
        query_terms = _tokens(text)
        if not query_terms:
            continue
        for keyword in keywords:
            matched = [query for query in query_terms if _term_match(query, keyword)]
            if matched:
                score += 1.0 * weight
                ranked += 1.0 * weight * rarity(matched)
                keyword_hits += 1
        for term in content_terms:
            if term in keywords:
                continue
            matched = [query for query in query_terms if _term_match(query, term)]
            if matched:
                score += 0.5 * weight
                ranked += 0.5 * weight * rarity(matched)
    return score, keyword_hits, ranked


def _direct_mention(annotation: MemoryAnnotation, message: str) -> bool:
    text = normalize_text(message)
    return any(
        len(normalize_text(word)) >= 2 and normalize_text(word) in text
        for word in annotation.keywords
    )


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", normalize_text(text))
    return {compact[index:index + 2] for index in range(len(compact) - 1)}


def _similar(left: MemoryItem, right: MemoryItem) -> bool:
    a, b = _bigrams(left.content), _bigrams(right.content)
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


def _same_space(item: MemoryItem, scope: Scope) -> bool:
    if item.origin_realm != scope.realm:
        return False
    return item.origin_public_at_capture or item.origin_channel_id == str(scope.channel_id)


def _use_hint(tier: str, annotation: MemoryAnnotation, status: StatusView) -> str:
    if tier == "T1":
        return "follow_up_optional"
    if annotation.usage_mode == UsageMode.SENSITIVE:
        return "only_if_user_raises"
    if annotation.usage_mode == UsageMode.AMBIENT and status.status in {
        EffectiveStatus.CURRENT, EffectiveStatus.UPCOMING, EffectiveStatus.RECENT_EVENT,
    }:
        return "free"
    return "if_relevant"


def _cross_space_boundary(item: MemoryItem, annotation: MemoryAnnotation, state: str) -> bool:
    """§7.2: verified boundaries (or unannotated legacy ones) apply in every space."""

    if annotation.usage_mode != UsageMode.DIRECTIVE or annotation.directive_type != (
        DirectiveType.BOUNDARY
    ):
        return False
    return annotation.verified or (state == "missing" and item.kind == MemoryKind.BOUNDARY)


def _confirmed_key(annotation: MemoryAnnotation) -> float:
    moment = _parse_time(annotation.last_confirmed_at) or _parse_time(annotation.observed_at)
    return moment.timestamp() if moment else 0.0


def select_personal_memory(
    items, annotations: dict[int, MemoryAnnotation], ctx: SelectionContext
) -> MemorySelection:
    """Choose T0 directives, T1 follow-ups, T2 relevant notes and T4 authorized items."""

    selection = MemorySelection()
    owner = str(ctx.scope.user_id)
    candidates: list[tuple[MemoryItem, MemoryAnnotation, str, StatusView]] = []
    quiet: list[tuple[MemoryItem, MemoryAnnotation]] = []
    followup_opt_out = False
    for item in items:
        if item.user_id != owner or item.kind == MemoryKind.RELATIONSHIP:
            if item.kind == MemoryKind.RELATIONSHIP and item.user_id == owner:
                selection.excluded["relationship_raw"] += 1
            continue
        if item.status != MemoryStatus.ACTIVE:
            selection.excluded["inactive"] += 1
            continue
        annotation, state = effective_annotation(item, annotations.get(item.id))
        is_boundary = _cross_space_boundary(item, annotation, state)
        if is_boundary and annotation.slot == PROACTIVE_OPT_OUT_SLOT:
            followup_opt_out = True
        access = memory_access(
            item, ctx.scope, explicitly_referenced=item.id in ctx.authorized_item_ids
        )
        if access != MemoryAccess.FULL:
            if is_boundary:
                quiet.append((item, annotation))
            else:
                selection.excluded["permission"] += 1
            continue
        status = effective_status(annotation, ctx.now)
        candidates.append((item, annotation, state, status))

    candidates = _cluster_representatives(candidates, selection)

    # T0: directives that are FULL here, plus quiet cross-space boundaries.
    directives = [
        (item, annotation, "directives") for item, annotation, _state, _status in candidates
        if annotation.usage_mode == UsageMode.DIRECTIVE
    ] + [(item, annotation, "quiet_boundaries") for item, annotation in quiet]
    directives.sort(key=lambda entry: (
        not entry[1].explicit,
        entry[1].directive_type != DirectiveType.BOUNDARY,
        -_confirmed_key(entry[1]),
    ))
    used_chars = 0
    for item, annotation, placement in directives:
        if len(selection.by_placement("directives")) + len(
            selection.by_placement("quiet_boundaries")
        ) >= T0_MAX_ITEMS or used_chars + len(item.content) > T0_MAX_CHARS:
            selection.excluded["t0_cap"] += 1
            continue
        used_chars += len(item.content)
        selection.selected.append(SelectedMemory(
            item, annotation, "T0", placement, effective_status(annotation, ctx.now), "always",
        ))

    notes = [
        (item, annotation, status) for item, annotation, _state, status in candidates
        if annotation.usage_mode != UsageMode.DIRECTIVE
    ]
    chosen: set[int] = set()

    # T4: reference-gated items the owner explicitly brought up this turn.
    for item, annotation, status in notes:
        if item.id in ctx.authorized_item_ids and memory_access(item, ctx.scope) != (
            MemoryAccess.FULL
        ):
            chosen.add(item.id)
            selection.selected.append(SelectedMemory(
                item, annotation, "T4", "notes", status, "if_relevant",
            ))

    _select_followups(notes, ctx, selection, chosen, followup_opt_out)
    _select_relevant(notes, ctx, selection, chosen)
    return selection


def _cluster_representatives(candidates, selection):
    """One member per cluster among the visible ones: the canonical, else the freshest."""

    groups: dict[int, list] = {}
    for entry in candidates:
        cluster = entry[1].cluster_id
        if cluster is not None:
            groups.setdefault(cluster, []).append(entry)
    keep: set[int] = set()
    for cluster, members in groups.items():
        canonical = [entry for entry in members if entry[0].id == cluster]
        chosen = canonical[0] if canonical else max(
            members, key=lambda entry: _confirmed_key(entry[1])
        )
        keep.add(chosen[0].id)
        selection.excluded["cluster"] += len(members) - 1
    return [
        entry for entry in candidates
        if entry[1].cluster_id is None or entry[0].id in keep
    ]


def _cooling_down(annotation: MemoryAnnotation, ctx: SelectionContext) -> bool:
    used = _parse_time(annotation.last_used_at)
    return (
        used is not None
        and ctx.now - used < COOLDOWN
        and not _direct_mention(annotation, ctx.message)
    )


def _select_followups(notes, ctx, selection, chosen, opt_out) -> None:
    mode = ctx.session_followup if ctx.session_followup in FOLLOWUP_MODES else "dm"
    in_server = ctx.scope.guild_id is not None
    new_session = (
        ctx.hours_since_last_turn is None
        or ctx.hours_since_last_turn * 3600 >= SESSION_GAP.total_seconds()
    )
    if (
        mode == "off"
        or (in_server and (mode != "all" or not ctx.explicit_call))
        or not new_session
        or opt_out
        or not is_light_chat(ctx.message)
    ):
        return
    selection.followup_attempted = True
    ranked = []
    for item, annotation, status in notes:
        if item.id in chosen or annotation.usage_mode in {UsageMode.SENSITIVE, UsageMode.DIRECTIVE}:
            continue
        rank = _followup_rank(item, annotation, status, ctx)
        if rank is None:
            continue
        if in_server and not (
            _same_space(item, ctx.scope)
            and annotation.usage_mode == UsageMode.AMBIENT
            and annotation.audience == Audience.SERVER
        ):
            selection.excluded["t1_space"] += 1
            continue
        attempts = annotation.proactive_count if annotation.proactive_status == status.status else 0
        if attempts >= PROACTIVE_COUNT_MAX:
            selection.excluded["t1_attempts"] += 1
            continue
        if _cooling_down(annotation, ctx):
            selection.excluded["cooldown"] += 1
            continue
        ranked.append((rank, -_confirmed_key(annotation), item, annotation, status))
    ranked.sort(key=lambda entry: entry[:2])
    limit = 1 if in_server else T1_MAX_CANDIDATES
    for _rank, _key, item, annotation, status in ranked[:limit]:
        chosen.add(item.id)
        selection.selected.append(SelectedMemory(
            item, annotation, "T1", "notes", status, "follow_up_optional",
        ))
    selection.excluded["t1_cap"] += max(0, len(ranked) - limit)


def _followup_rank(item, annotation, status, ctx) -> int | None:
    if status.status == EffectiveStatus.JUST_PASSED:
        return 0
    if status.status == EffectiveStatus.UPCOMING and status.date and annotation.time_scope == (
        TimeScope.PLANNED
    ):
        bounds = _event_bounds(status.date)
        if bounds and bounds[0] - ctx.now.date() <= T1_UPCOMING_WINDOW:
            return 1
    observed = _parse_time(annotation.observed_at)
    if (
        annotation.usage_mode == UsageMode.AMBIENT
        and annotation.importance >= 3
        and status.status == EffectiveStatus.CURRENT
        and observed is not None
        and ctx.now - observed <= T1_RECENT_AMBIENT_WINDOW
    ):
        return 2
    return None


_FRESHNESS = {
    EffectiveStatus.CURRENT: 1.0, EffectiveStatus.UPCOMING: 1.0,
    EffectiveStatus.JUST_PASSED: 1.0, EffectiveStatus.RECENT_EVENT: 0.9,
    EffectiveStatus.UNVERIFIED: 0.6, EffectiveStatus.PAST: 0.5, EffectiveStatus.PAST_EVENT: 0.5,
}
_IMPORTANCE = {1: 0.8, 2: 1.0, 3: 1.2}


def _select_relevant(notes, ctx, selection, chosen) -> None:
    current = ((ctx.message, 1.0),)
    with_chain = current + tuple((text, 0.5) for text in ctx.reply_texts)
    idf = query_idf([(item, annotation) for item, annotation, _status in notes], with_chain)
    scored = []
    for item, annotation, status in notes:
        if item.id in chosen:
            continue
        if status.expired:
            selection.excluded["expired"] += 1
            continue
        usage = annotation.usage_mode
        if usage == UsageMode.SENSITIVE:
            score, hits, ranked = _relevance(item, annotation, current, idf)
            admitted = hits > 0
        elif usage == UsageMode.CONTEXTUAL:
            score, hits, ranked = _relevance(item, annotation, with_chain, idf)
            admitted = hits > 0 or (ctx.task_register and score >= AMBIENT_THRESHOLD)
        else:
            score, hits, ranked = _relevance(item, annotation, with_chain, idf)
            threshold = AMBIENT_THRESHOLD - (EXPLICIT_RELIEF if annotation.explicit else 0.0)
            admitted = score >= threshold
        if not admitted:
            if score > 0:
                selection.excluded["threshold"] += 1
            continue
        if _cooling_down(annotation, ctx):
            selection.excluded["cooldown"] += 1
            continue
        weight = ranked * _FRESHNESS[status.status] * _IMPORTANCE.get(annotation.importance, 1.0)
        scored.append((weight, _confirmed_key(annotation), item, annotation, status))
    scored.sort(key=lambda entry: entry[:2], reverse=True)
    picked: list[MemoryItem] = []
    used_chars = 0
    for _weight, _key, item, annotation, status in scored:
        if any(_similar(item, other) for other in picked):
            selection.excluded["similarity"] += 1
            continue
        if len(picked) >= T2_MAX_ITEMS or used_chars + len(item.content) > T2_MAX_CHARS:
            selection.excluded["t2_cap"] += 1
            continue
        picked.append(item)
        used_chars += len(item.content)
        chosen.add(item.id)
        selection.selected.append(SelectedMemory(
            item, annotation, "T2", "notes", status, _use_hint("T2", annotation, status),
        ))


def render_user_memory(selection: MemorySelection, scope: Scope) -> dict:
    """The provider-facing block: text, state and usage hints; no ids, kinds or scores."""

    notes = []
    for entry in selection.by_placement("notes"):
        note = {"text": entry.item.content, "status": entry.status.status.value}
        if entry.status.date:
            note["date"] = entry.status.date
        elif entry.status.noted:
            note["noted"] = entry.status.noted
        note["use"] = entry.use
        relation = _scope_relation(entry.item, scope)
        if relation != "current_channel":
            note["scope_relation"] = relation
        notes.append(note)
    return {
        "directives": [entry.item.content for entry in selection.by_placement("directives")],
        "quiet_boundaries": [
            entry.item.content for entry in selection.by_placement("quiet_boundaries")
        ],
        "notes": notes,
    }


def selection_provenance(selection: MemorySelection) -> dict:
    """Content-free record of what was chosen and why others were not."""

    return {
        "items": [
            {"store": "personal", "item_id": entry.item.id, "tier": entry.tier,
             "placement": entry.placement, "status": entry.status.status.value}
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


def _used_in(entry: SelectedMemory, answer: str) -> bool:
    """Decision 19: a memory counts as used when the reply contains one of its keywords."""

    text = normalize_text(answer)
    keywords = [normalize_text(word) for word in entry.annotation.keywords]
    keywords = [word for word in keywords if len(word) >= 2]
    if keywords:
        return any(word in text for word in keywords)
    # Unannotated legacy items have no keywords: require two content words instead.
    return sum(term in text for term in _tokens(entry.item.content)) >= 2


def memory_usage(selection: MemorySelection, answer: str, now: datetime) -> list[MemoryUsage]:
    """Cooldown and follow-up bookkeeping for the notes injected into this reply."""

    stamp = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    updates = []
    for entry in selection.selected:
        if entry.placement != "notes":
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


def recall_personal_memory(
    items, annotations: dict[int, MemoryAnnotation], ctx: SelectionContext, query: str,
    *, limit: int = 6,
) -> list[SelectedMemory]:
    """Model-initiated lookup (§7 T3): FULL items plus this turn's T4 approvals only."""

    owner = str(ctx.scope.user_id)
    pool = []
    for item in items:
        if (
            item.user_id != owner
            or item.kind == MemoryKind.RELATIONSHIP
            or item.status != MemoryStatus.ACTIVE
        ):
            continue
        access = memory_access(
            item, ctx.scope, explicitly_referenced=item.id in ctx.authorized_item_ids
        )
        if access != MemoryAccess.FULL:
            continue
        pool.append((item, effective_annotation(item, annotations.get(item.id))[0]))
    texts = ((query, 1.0),)
    idf = query_idf(pool, texts)
    found = []
    for item, annotation in pool:
        score, _hits, ranked = _relevance(item, annotation, texts, idf)
        if score <= 0:
            continue
        status = effective_status(annotation, ctx.now)
        if status.expired:
            status = replace(status, status=EffectiveStatus.PAST, expired=False)
        found.append((ranked, _confirmed_key(annotation), item, annotation, status))
    found.sort(key=lambda entry: entry[:2], reverse=True)
    return [
        SelectedMemory(item, annotation, "T3", "notes", status,
                       _use_hint("T3", annotation, status))
        for _score, _key, item, annotation, status in found[:limit]
    ]


__all__ = [
    "COOLDOWN",
    "FOLLOWUP_MODES",
    "SESSION_GAP",
    "STABILITY_WINDOWS",
    "EffectiveStatus",
    "MemorySelection",
    "MemoryUsage",
    "SelectedMemory",
    "SelectionContext",
    "StatusView",
    "effective_annotation",
    "effective_status",
    "is_light_chat",
    "memory_usage",
    "recall_personal_memory",
    "render_user_memory",
    "select_personal_memory",
    "selection_provenance",
]
