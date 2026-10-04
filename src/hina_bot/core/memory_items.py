from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math

from .routing import Scope


class MemoryKind(StrEnum):
    FACT = "fact"
    EVENT = "event"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"
    BOUNDARY = "boundary"
    TASK = "task"


class MemoryDisclosure(StrEnum):
    LOCAL = "local"
    IMPLICIT = "implicit"
    REFERENCE_GATED = "reference_gated"
    GLOBAL = "global"


class MemoryAccess(StrEnum):
    HIDDEN = "hidden"
    IMPLICIT = "implicit"
    FULL = "full"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


MEMORY_CONTENT_MAX_CHARS = 600


RELATIONSHIP_EVIDENCE_AXES = (
    "familiarity",
    "comfort",
    "casualness",
    "teasing_tolerance",
    "support_openness",
    "task_orientation",
)


@dataclass(frozen=True)
class RelationshipEvidence:
    """Sparse 0..4 evidence vector attached only to relationship memories.

    Zero means "no positive evidence stored for this axis", never a negative preference.
    """

    familiarity: int = 0
    comfort: int = 0
    casualness: int = 0
    teasing_tolerance: int = 0
    support_openness: int = 0
    task_orientation: int = 0

    def __post_init__(self) -> None:
        for axis in RELATIONSHIP_EVIDENCE_AXES:
            value = getattr(self, axis)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
                raise ValueError(f"{axis} relationship evidence must be an integer from 0 to 4")

    @classmethod
    def from_mapping(cls, raw) -> RelationshipEvidence:
        if raw in (None, {}):
            return cls()
        if not isinstance(raw, dict):
            raise TypeError("relationship_evidence must be an object")
        unknown = set(raw) - set(RELATIONSHIP_EVIDENCE_AXES)
        if unknown:
            raise ValueError("Unknown relationship evidence axis")
        values = {}
        for axis, value in raw.items():
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
                raise ValueError(f"{axis} relationship evidence must be an integer from 0 to 4")
            values[axis] = value
        return cls(**values)

    def as_dict(self) -> dict[str, int]:
        return {
            axis: getattr(self, axis)
            for axis in RELATIONSHIP_EVIDENCE_AXES
            if getattr(self, axis) > 0
        }

    def __bool__(self) -> bool:
        return any(getattr(self, axis) > 0 for axis in RELATIONSHIP_EVIDENCE_AXES)


@dataclass(frozen=True)
class ValidatedMemoryFields:
    content: str
    kind: MemoryKind
    disclosure: MemoryDisclosure
    confidence: float
    relationship_evidence: RelationshipEvidence


def validate_memory_item_fields(
    content: str,
    *,
    kind: MemoryKind | str,
    disclosure: MemoryDisclosure | str,
    confidence: float,
    relationship_evidence: RelationshipEvidence | dict | None = None,
) -> ValidatedMemoryFields:
    """Validate and normalize the editable structured-memory fields."""

    if not isinstance(content, str):
        raise TypeError("Memory item content must be text")
    text = content.strip()
    if not text:
        raise ValueError("Memory item content must not be empty")
    if len(text) > MEMORY_CONTENT_MAX_CHARS:
        raise ValueError(
            f"Memory item content must be at most {MEMORY_CONTENT_MAX_CHARS} characters"
        )
    memory_kind = MemoryKind(kind)
    memory_disclosure = MemoryDisclosure(disclosure)
    memory_confidence = float(confidence)
    if not math.isfinite(memory_confidence) or not 0 <= memory_confidence <= 1:
        raise ValueError("Memory item confidence must be between 0 and 1")
    if isinstance(relationship_evidence, RelationshipEvidence):
        evidence = relationship_evidence
    else:
        evidence = RelationshipEvidence.from_mapping(relationship_evidence)
    if memory_kind != MemoryKind.RELATIONSHIP and evidence:
        raise ValueError("relationship_evidence is valid only for relationship memory")
    return ValidatedMemoryFields(
        content=text,
        kind=memory_kind,
        disclosure=memory_disclosure,
        confidence=memory_confidence,
        relationship_evidence=evidence,
    )


@dataclass(frozen=True)
class MemoryItem:
    id: int
    user_id: str
    content: str
    kind: MemoryKind
    origin_realm: str
    origin_channel_id: str
    origin_public_at_capture: bool
    disclosure: MemoryDisclosure
    source_message_ids: tuple[str, ...]
    confidence: float
    created_at: str
    updated_at: str
    revision: int = 0
    relationship_evidence: RelationshipEvidence = field(default_factory=RelationshipEvidence)
    user_name: str = ""
    status: MemoryStatus = MemoryStatus.ACTIVE
    superseded_by: int | None = None


def _same_disclosure_space(item: MemoryItem, current_scope: Scope) -> bool:
    if item.origin_realm != current_scope.realm:
        return False
    if not item.origin_realm.startswith("guild:"):
        return True
    if item.origin_public_at_capture:
        return True
    return item.origin_channel_id == str(current_scope.channel_id)


def memory_access(
    item: MemoryItem,
    current_scope: Scope,
    *,
    explicitly_referenced: bool = False,
) -> MemoryAccess:
    """Return how much of one memory item may reach the current response context.

    This is a pure policy primitive. Phase 1 does not wire it into request assembly yet.
    The current speaker must own the item before any cross-space rule is considered.
    Structured owner memories are always full in that owner's DM.
    """

    if item.user_id != str(current_scope.user_id):
        return MemoryAccess.HIDDEN

    # The owner's DM is their private aggregate memory space. It may use that
    # owner's structured memories from any origin, but never another user's.
    if current_scope.guild_id is None:
        return MemoryAccess.FULL

    if _same_disclosure_space(item, current_scope):
        return MemoryAccess.FULL

    if item.disclosure == MemoryDisclosure.GLOBAL:
        return MemoryAccess.FULL

    if item.disclosure == MemoryDisclosure.IMPLICIT:
        return MemoryAccess.IMPLICIT

    if item.disclosure == MemoryDisclosure.REFERENCE_GATED and explicitly_referenced:
        return MemoryAccess.FULL

    return MemoryAccess.HIDDEN


__all__ = [
    "MEMORY_CONTENT_MAX_CHARS",
    "RELATIONSHIP_EVIDENCE_AXES",
    "MemoryAccess",
    "MemoryDisclosure",
    "MemoryItem",
    "MemoryKind",
    "RelationshipEvidence",
    "ValidatedMemoryFields",
    "validate_memory_item_fields",
    "memory_access",
]
