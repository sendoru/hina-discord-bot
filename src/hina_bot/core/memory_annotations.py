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
_RECURRENCE = re.compile(
    r"weekly:(?:MON|TUE|WED|THU|FRI|SAT|SUN) (?:[01]\d|2[0-3]):[0-5]\d"
    r"|monthly:(?:0[1-9]|[12]\d|3[01])"
)
# Past or completed markers in the quoted user utterance (#449 §5 tense cues).
_PAST_MARKERS = re.compile(
    r"었었|았었|였었|던\s*적|시절|때는|예전에|옛날에|어릴\s*때|작년|그만뒀|그만두었|끝났|끝냈|"
    r"관뒀|그만\s*뒀"
)

LINT_EVIDENCE_MISMATCH = "evidence_mismatch"
LINT_PAST_MARKER = "past_marker_ongoing"
LINT_UNDATED_EVENT = "undated_event"
LINT_INVALID_EVENT_AT = "invalid_event_at"
LINT_INVALID_RECURRENCE = "invalid_recurrence"
LINT_DEFAULTED = "annotation_defaulted"
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
    return bool(_RECURRENCE.fullmatch(value))


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


def legacy_annotation_defaults(kind: str) -> tuple[UsageMode, DirectiveType | None]:
    """Principle 6: missing judgments use the quieter reading, except boundaries persist."""

    if kind == "boundary":
        return UsageMode.DIRECTIVE, DirectiveType.BOUNDARY
    return UsageMode.CONTEXTUAL, None


__all__ = [
    "EVIDENCE_MAX_CHARS",
    "KEYWORDS_MAX",
    "LINT_AUDIENCE_NARROWED",
    "LINT_DEFAULTED",
    "LINT_DIRECTIVE_INCOMPLETE",
    "LINT_DIRECTIVE_REJECTED",
    "LINT_DIRECTIVE_UNVERIFIED",
    "LINT_EVIDENCE_MISMATCH",
    "LINT_INVALID_EVENT_AT",
    "LINT_INVALID_RECURRENCE",
    "LINT_PAST_MARKER",
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
