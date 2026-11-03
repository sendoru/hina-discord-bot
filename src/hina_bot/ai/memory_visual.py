"""Image inputs for the structured-memory extractor (#472).

The extractor never sees an image unless a stored turn has one attached. Images only
enrich what the user said about a subject (species, appearance, a title shown on a
screenshot); they are untrusted data and never a standalone source of a memory.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from dataclasses import dataclass

# One extraction call covers a four-turn batch. Keep the request small enough for inline
# image upload and bound the token cost of a batch that happens to contain photos.
MEMORY_VISUAL_MAX_IMAGES = 4
MEMORY_VISUAL_MAX_BYTES = 8 * 1024 * 1024
# A detail read from a photo is less certain than something the user stated.
MEMORY_VISUAL_CONFIDENCE_CAP = 0.75
_SUPPORTED_MIME = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_METADATA_LABEL = "Untrusted visual evidence metadata(JSON):"


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


def select_memory_visuals(
    visuals: Iterable[MemoryVisual],
    *,
    max_images: int = MEMORY_VISUAL_MAX_IMAGES,
    max_bytes: int = MEMORY_VISUAL_MAX_BYTES,
) -> list[MemoryVisual]:
    """Keep the most recent images that fit the limits, in their original order.

    ``visuals`` is chronological. Newer turns win because older ones are closer to a
    stale cut-off; an image that does not fit is skipped, not truncated.
    """

    chosen: list[MemoryVisual] = []
    used = 0
    for visual in reversed(list(visuals)):
        if len(chosen) >= max_images:
            break
        size = len(visual.data)
        if not size or visual.mime_type not in _SUPPORTED_MIME or used + size > max_bytes:
            continue
        chosen.append(visual)
        used += size
    chosen.reverse()
    return chosen


def visual_message_ids(visuals: Iterable[MemoryVisual]) -> frozenset[str]:
    """Message ids of the turns that actually have an image in this request."""

    return frozenset(visual.message_id for visual in visuals)


def extraction_input(
    payload_text: str,
    visuals: Iterable[MemoryVisual] = (),
    *,
    include_media_resolution: bool = False,
):
    """Return the extractor ``input``: the payload string, or blocks when images exist."""

    images = list(visuals)
    if not images:
        return payload_text
    content: list[dict] = [{"type": "input_text", "text": payload_text}]
    for visual in images:
        descriptor = {"message_id": visual.message_id, "index": visual.index}
        content.append({
            "type": "input_text",
            "text": _METADATA_LABEL + json.dumps(
                descriptor, ensure_ascii=False, separators=(",", ":")
            ),
        })
        block = {"type": "input_image", "image_url": visual.data_url(), "detail": "auto"}
        if include_media_resolution:
            block["resolution"] = "medium"
        content.append(block)
    return [{"role": "user", "content": content}]


__all__ = [
    "MEMORY_VISUAL_CONFIDENCE_CAP",
    "MEMORY_VISUAL_MAX_BYTES",
    "MEMORY_VISUAL_MAX_IMAGES",
    "MemoryVisual",
    "extraction_input",
    "select_memory_visuals",
    "visual_message_ids",
]
