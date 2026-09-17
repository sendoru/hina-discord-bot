from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class MemoryItem:
    id: int
    user_id: str
    content: str
    kind: MemoryKind
    origin_realm: str
    origin_channel_id: str
    disclosure: MemoryDisclosure
    source_message_ids: tuple[str, ...]
    confidence: float
    created_at: str
    updated_at: str


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
    """

    if item.user_id != str(current_scope.user_id):
        return MemoryAccess.HIDDEN

    if item.origin_realm == current_scope.realm:
        return MemoryAccess.FULL

    if item.disclosure == MemoryDisclosure.GLOBAL:
        return MemoryAccess.FULL

    if item.disclosure == MemoryDisclosure.IMPLICIT:
        return MemoryAccess.IMPLICIT

    if item.disclosure == MemoryDisclosure.REFERENCE_GATED:
        # Preserve the existing one-way public-server -> DM inheritance switch. A public fact can
        # still be remembered privately without making it available in a different public server.
        origin_is_guild = item.origin_realm.startswith("guild:")
        current_is_dm = current_scope.guild_id is None
        if origin_is_guild and current_is_dm and public_server_memory_in_dm:
            return MemoryAccess.FULL
        if explicitly_referenced:
            return MemoryAccess.FULL

    return MemoryAccess.HIDDEN


__all__ = [
    "MemoryAccess",
    "MemoryDisclosure",
    "MemoryItem",
    "MemoryKind",
    "memory_access",
]
