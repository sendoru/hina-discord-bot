"""Photos held until the memory extractor has read them (#472).

Image bytes are never part of a stored turn. A user's own photo is kept here only for the
turns still waiting for structured-memory extraction, then deleted. Every operation is a
no-op in the base class, so a ``Store`` with nothing to keep is the null store and callers
need no feature checks.

The interface speaks in plain strings (realm, channel, user, message id) rather than
``Scope`` so the same calls serve a purge command, a Discord delete event and an offline
tool that only has the database.
"""

from __future__ import annotations

import base64
from collections.abc import Collection, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryVisual:
    """One image attached to a pending turn, addressed by that turn's message id."""

    message_id: str
    index: int
    mime_type: str
    data: bytes

    def data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


class PendingVisualStore:
    """Interface and null implementation: stores nothing, deletes nothing."""

    def put(
        self, realm: str, channel_id: str, user_id: str, message_id: str,
        visuals: Iterable[MemoryVisual],
    ) -> int:
        """Keep the images of one turn; return how many were stored."""
        return 0

    def get(
        self, realm: str, channel_id: str, user_id: str, message_ids: Collection[str],
    ) -> dict[str, list[MemoryVisual]]:
        """Return stored images by turn message id; turns without images are absent."""
        return {}

    def discard_messages(
        self, realm: str, channel_id: str, user_id: str, message_ids: Collection[str],
    ) -> int:
        """Delete the images of specific turns (extracted, or their message was deleted)."""
        return 0

    def discard_user(self, realm: str, user_id: str) -> int:
        """Delete one user's images in every channel of a realm (``/forget``)."""
        return 0

    def discard_channel(self, realm: str, channel_id: str) -> int:
        """Delete every user's images in one channel."""
        return 0

    def discard_realm(self, realm: str) -> int:
        """Delete every image formed in a guild or DM realm."""
        return 0

    def discard_all(self) -> int:
        """Delete every stored image."""
        return 0

    def sweep(
        self,
        valid: Collection[tuple[str, str, str, str]],
        *,
        max_age_seconds: float,
        now: float | None = None,
    ) -> int:
        """Delete images whose turn is no longer pending, and images older than the TTL.

        ``valid`` holds ``(realm, channel_id, user_id, message_id)`` of the turns still
        waiting for extraction.
        """
        return 0


class NullPendingVisualStore(PendingVisualStore):
    """Explicit name for the default store."""


__all__ = ["MemoryVisual", "NullPendingVisualStore", "PendingVisualStore"]
