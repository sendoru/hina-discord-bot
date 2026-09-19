from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

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
    def from_mapping(cls, raw) -> "RelationshipEvidence":
        if raw in (None, {}):
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("relationship_evidence must be an object")
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
    relationship_evidence: RelationshipEvidence = field(default_factory=RelationshipEvidence)


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
    public_server_memory_in_dm: bool = True,
) -> MemoryAccess:
    """Return how much of one memory item may reach the current response context.

    This is a pure policy primitive. Phase 1 does not wire it into request assembly yet.
    The current speaker must own the item before any cross-space rule is considered.
    The public-server-to-DM flag is retained for compatibility with legacy callers;
    structured owner memories are always full in that owner's DM.
    """
    _ = public_server_memory_in_dm

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
    "RELATIONSHIP_EVIDENCE_AXES",
    "MemoryAccess",
    "MemoryDisclosure",
    "MemoryItem",
    "MemoryKind",
    "RelationshipEvidence",
    "memory_access",
]
