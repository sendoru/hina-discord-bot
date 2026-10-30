"""Memory v2 annotations (#449 §4.1): regenerable judgments about one memory item.

An annotation never adds a new fact. It records when an item applies, how Hina may
use it and where it came from. The rules here mirror the database CHECK constraints
so writers can normalize values before SQLite rejects them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class TimeScope(StrEnum):
    ONGOING = "ongoing"
    PAST = "past"
    PLANNED = "planned"
    EPISODIC = "episodic"
    RECURRING = "recurring"
    UNKNOWN = "unknown"


class Stability(StrEnum):
    PERMANENT = "permanent"
    LONG = "long"
    MEDIUM = "medium"
    SHORT = "short"


class UsageMode(StrEnum):
    DIRECTIVE = "directive"
    AMBIENT = "ambient"
    CONTEXTUAL = "contextual"
    SENSITIVE = "sensitive"


class DirectiveType(StrEnum):
    BOUNDARY = "boundary"
    STYLE = "style"


class Audience(StrEnum):
    OWNER = "owner"
    SERVER = "server"


class Annotator(StrEnum):
    EXTRACTOR = "extractor"
    CONSOLIDATOR = "consolidator"
    BACKFILL = "backfill"
    ADMIN = "admin"


EVIDENCE_MAX_CHARS = 120
SLOT_MAX_CHARS = 40
KEYWORD_MAX_CHARS = 30
KEYWORDS_MAX = 8
PROACTIVE_COUNT_MAX = 2
# Planned/episodic items without a date expire this long after observation (P2 reads it).
UNDATED_EVENT_EXPIRY = timedelta(days=30)

_EVENT_AT = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
# Several weekdays may share one rule ("주말마다" -> weekly:SAT,SUN), and the time is
# optional: "매주 화요일 저녁" has a weekday but no clock time.
_WEEKDAY = r"(?:MON|TUE|WED|THU|FRI|SAT|SUN)"
_RECURRENCE = re.compile(
    rf"weekly:{_WEEKDAY}(?:,{_WEEKDAY})*(?: (?:[01]\d|2[0-3]):[0-5]\d)?"
    r"|monthly:(?:0[1-9]|[12]\d|3[01])"
)
# Past or completed markers in the quoted user utterance (#449 §5 tense cues).
_PAST_MARKERS = re.compile(
    r"었었|았었|였었|던\s*적|시절|때는|예전에|옛날에|어릴\s*때|작년|그만뒀|그만두었|끝났|끝냈|"
    r"관뒀|그만\s*뒀"
)

LINT_EVIDENCE_MISMATCH = "evidence_mismatch"
LINT_SOURCE_UNAVAILABLE = "source_unavailable"
LINT_PAST_MARKER = "past_marker_ongoing"
LINT_UNDATED_EVENT = "undated_event"
LINT_INVALID_EVENT_AT = "invalid_event_at"
LINT_INVALID_RECURRENCE = "invalid_recurrence"
LINT_RECURRENCE_MISSING = "recurrence_missing"
LINT_DEFAULTED = "annotation_defaulted"
# Invalid legacy item fields replaced by the quietest value instead of dropping the item.
LINT_DISCLOSURE_DEFAULTED = "disclosure_defaulted"
LINT_KIND_DEFAULTED = "kind_defaulted"
LINT_DIRECTIVE_INCOMPLETE = "directive_without_type"
LINT_DIRECTIVE_REJECTED = "directive_rejected"
LINT_DIRECTIVE_UNVERIFIED = "directive_unverified"
LINT_AUDIENCE_NARROWED = "audience_narrowed"


@dataclass(frozen=True)
class MemoryAnnotation:
    """One stored annotation row; ``memory_item_id`` is 0 until the item exists."""

    memory_item_id: int
    item_revision: int
    time_scope: TimeScope
    observed_at: str
    last_confirmed_at: str
    usage_mode: UsageMode
    annotator: Annotator
    annotator_version: str
    stability: Stability | None = None
    event_at: str | None = None
    recurrence: str | None = None
    directive_type: DirectiveType | None = None
    audience: Audience = Audience.OWNER
    importance: int = 2
    slot: str = ""
    keywords: tuple[str, ...] = ()
    evidence: str = ""
    explicit: bool = False
    verified: bool = False
    lint_flags: tuple[str, ...] = field(default_factory=tuple)
    cluster_id: int | None = None
    confirmations: int = 1
    last_used_at: str | None = None
    proactive_status: str = ""
    proactive_count: int = 0
    last_proactive_at: str | None = None
    updated_at: str = ""

    def is_stale(self, item_revision: int) -> bool:
        """An admin edit raised the item revision after this annotation was written."""

        return self.item_revision < int(item_revision)


def normalize_text(text: str) -> str:
    """Compare quotes loosely: width, case and whitespace never decide a match."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text)).casefold()).strip()


def evidence_matches(evidence: str, utterances) -> bool:
    quote = normalize_text(evidence)
    return bool(quote) and any(quote in normalize_text(text) for text in utterances)


def has_past_marker(text: str) -> bool:
    return bool(_PAST_MARKERS.search(str(text)))


def valid_event_at(value: str) -> bool:
    if not _EVENT_AT.fullmatch(value):
        return False
    parts = [int(part) for part in value.split("-")]
    try:
        date(parts[0], parts[1], parts[2] if len(parts) == 3 else 1)
    except ValueError:
        return False
    return True


def valid_recurrence(value: str) -> bool:
    if not _RECURRENCE.fullmatch(value):
        return False
    if value.startswith("weekly:"):
        days = value.removeprefix("weekly:").split(" ")[0].split(",")
        return len(days) == len(set(days))
    return True


def clean_keywords(values) -> tuple[str, ...]:
    if not isinstance(values, list | tuple):
        return ()
    cleaned = []
    for value in values:
        if not isinstance(value, str):
            continue
        word = value.strip()[:KEYWORD_MAX_CHARS]
        if word and word not in cleaned:
            cleaned.append(word)
    return tuple(cleaned[:KEYWORDS_MAX])


def local_turn_time(created_at: str, zone_name: str) -> tuple[str, str]:
    """Convert a stored UTC turn time into local ISO time and weekday for date reasoning."""

    try:
        moment = datetime.fromisoformat(str(created_at).replace(" ", "T"))
    except ValueError:
        return "", ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    try:
        local = moment.astimezone(ZoneInfo(zone_name or "Asia/Seoul"))
    except (KeyError, ValueError):
        local = moment.astimezone(ZoneInfo("Asia/Seoul"))
    return local.isoformat(timespec="minutes"), local.strftime("%a").upper()


def enforce_consistency(annotation: MemoryAnnotation, *, public_origin: bool) -> MemoryAnnotation:
    """Apply the cross-field rules the database also enforces, recording narrowing."""

    flags = list(annotation.lint_flags)
    stability = annotation.stability if annotation.time_scope == TimeScope.ONGOING else None
    usage = annotation.usage_mode
    directive_type = annotation.directive_type
    if usage == UsageMode.DIRECTIVE and directive_type is None:
        usage = UsageMode.CONTEXTUAL
        flags.append(LINT_DIRECTIVE_INCOMPLETE)
    if usage != UsageMode.DIRECTIVE:
        directive_type = None
    audience = annotation.audience
    if audience == Audience.SERVER and (usage == UsageMode.SENSITIVE or not public_origin):
        audience = Audience.OWNER
        flags.append(LINT_AUDIENCE_NARROWED)
    importance = min(3, max(1, int(annotation.importance)))
    if annotation.explicit:
        importance = 3
    return replace(
        annotation,
        stability=stability,
        usage_mode=usage,
        directive_type=directive_type,
        audience=audience,
        importance=importance,
        proactive_count=min(PROACTIVE_COUNT_MAX, max(0, int(annotation.proactive_count))),
        lint_flags=tuple(dict.fromkeys(flags)),
    )


ADMIN_ANNOTATOR_VERSION = "dashboard"


def _enum_field(enum, raw, name, *, optional=False):
    value = str(raw or "").strip()
    if not value and optional:
        return None
    try:
        return enum(value).value
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc


def admin_annotation_fields(raw) -> dict[str, object]:
    """Validate an admin edit into JSON-safe fields; raises ValueError naming the field.

    The dashboard pre-validates with this and the runtime re-validates the queued
    payload, so both sides share one definition of an acceptable admin judgment.
    """

    if not isinstance(raw, dict):
        raise TypeError("invalid annotation")
    time_scope = _enum_field(TimeScope, raw.get("time_scope"), "time_scope")
    stability = _enum_field(Stability, raw.get("stability"), "stability", optional=True)
    if stability is not None and time_scope != TimeScope.ONGOING.value:
        raise ValueError("stability applies only to ongoing memories")
    usage_mode = _enum_field(UsageMode, raw.get("usage_mode"), "usage_mode")
    directive_type = _enum_field(
        DirectiveType, raw.get("directive_type"), "directive_type", optional=True
    )
    if (usage_mode == UsageMode.DIRECTIVE.value) != (directive_type is not None):
        raise ValueError("directive_type is required exactly for directive memories")
    audience = _enum_field(Audience, raw.get("audience") or Audience.OWNER.value, "audience")
    if usage_mode == UsageMode.SENSITIVE.value and audience != Audience.OWNER.value:
        raise ValueError("sensitive memories are visible only to their owner")
    event_at = str(raw.get("event_at") or "").strip() or None
    if event_at is not None and not valid_event_at(event_at):
        raise ValueError("invalid event_at")
    recurrence = str(raw.get("recurrence") or "").strip() or None
    if recurrence is not None and not valid_recurrence(recurrence):
        raise ValueError("invalid recurrence")
    try:
        importance = int(raw.get("importance", 2))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid importance") from exc
    if not 1 <= importance <= 3:
        raise ValueError("invalid importance")
    slot = str(raw.get("slot") or "").strip()
    if len(slot) > SLOT_MAX_CHARS:
        raise ValueError("slot is too long")
    keywords = raw.get("keywords") or ()
    if isinstance(keywords, str):
        keywords = keywords.split(",")
    explicit = raw.get("explicit")
    return {
        "time_scope": time_scope,
        "stability": stability,
        "event_at": event_at,
        "recurrence": recurrence,
        "usage_mode": usage_mode,
        "directive_type": directive_type,
        "audience": audience,
        "importance": importance,
        "slot": slot,
        "keywords": list(clean_keywords(list(keywords))),
        "explicit": explicit is True or str(explicit).lower() in {"1", "true", "on", "yes"},
    }


def admin_annotation(
    fields: dict[str, object],
    *,
    memory_item_id: int,
    item_revision: int,
    observed_at: str,
    existing: MemoryAnnotation | None = None,
) -> MemoryAnnotation:
    """Build an admin annotation; lifecycle markers and the quote survive the edit."""

    base = existing or MemoryAnnotation(
        memory_item_id=int(memory_item_id),
        item_revision=int(item_revision),
        time_scope=TimeScope.UNKNOWN,
        observed_at=observed_at,
        last_confirmed_at=observed_at,
        usage_mode=UsageMode.CONTEXTUAL,
        annotator=Annotator.ADMIN,
        annotator_version=ADMIN_ANNOTATOR_VERSION,
    )
    return replace(
        base,
        memory_item_id=int(memory_item_id),
        item_revision=int(item_revision),
        time_scope=TimeScope(fields["time_scope"]),
        stability=Stability(fields["stability"]) if fields["stability"] else None,
        event_at=fields["event_at"],
        recurrence=fields["recurrence"],
        usage_mode=UsageMode(fields["usage_mode"]),
        directive_type=DirectiveType(fields["directive_type"]) if fields["directive_type"] else None,
        audience=Audience(fields["audience"]),
        importance=int(fields["importance"]),
        slot=str(fields["slot"]),
        keywords=tuple(fields["keywords"]),
        explicit=bool(fields["explicit"]),
        # An admin judgment is reviewed by definition; automated lint no longer applies.
        verified=True,
        lint_flags=(),
        annotator=Annotator.ADMIN,
        annotator_version=ADMIN_ANNOTATOR_VERSION,
    )


def legacy_annotation_defaults(kind: str) -> tuple[UsageMode, DirectiveType | None]:
    """Principle 6: missing judgments use the quieter reading, except boundaries persist."""

    if kind == "boundary":
        return UsageMode.DIRECTIVE, DirectiveType.BOUNDARY
    return UsageMode.CONTEXTUAL, None


__all__ = [
    "ADMIN_ANNOTATOR_VERSION",
    "EVIDENCE_MAX_CHARS",
    "KEYWORDS_MAX",
    "LINT_AUDIENCE_NARROWED",
    "LINT_DEFAULTED",
    "LINT_DIRECTIVE_INCOMPLETE",
    "LINT_DIRECTIVE_REJECTED",
    "LINT_DIRECTIVE_UNVERIFIED",
    "LINT_DISCLOSURE_DEFAULTED",
    "LINT_EVIDENCE_MISMATCH",
    "LINT_INVALID_EVENT_AT",
    "LINT_INVALID_RECURRENCE",
    "LINT_KIND_DEFAULTED",
    "LINT_PAST_MARKER",
    "LINT_RECURRENCE_MISSING",
    "LINT_SOURCE_UNAVAILABLE",
    "LINT_UNDATED_EVENT",
    "PROACTIVE_COUNT_MAX",
    "SLOT_MAX_CHARS",
    "UNDATED_EVENT_EXPIRY",
    "Annotator",
    "Audience",
    "DirectiveType",
    "MemoryAnnotation",
    "Stability",
    "TimeScope",
    "UsageMode",
    "admin_annotation",
    "admin_annotation_fields",
    "clean_keywords",
    "enforce_consistency",
    "evidence_matches",
    "has_past_marker",
    "legacy_annotation_defaults",
    "local_turn_time",
    "normalize_text",
    "valid_event_at",
    "valid_recurrence",
]
