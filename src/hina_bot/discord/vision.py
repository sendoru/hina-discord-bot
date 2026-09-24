"""Collect bounded image inputs from current, replied-to, and recent Discord messages."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import discord
import httpx

from hina_bot.ai.vision import VisualInput

log = logging.getLogger("hina")

MAX_VISUAL_BYTES = 5 * 1024 * 1024
MAX_TOTAL_VISUAL_BYTES = 12 * 1024 * 1024
MAX_CONTEXT_VISUAL_MESSAGES = 3
_CUSTOM_EMOJI = re.compile(r"<(?P<animated>a?):(?P<name>[^:<>\s]{1,32}):(?P<id>[0-9]{1,20})>")


@dataclass(frozen=True)
class VisualContextRef:
    """Content-free reference to a selected Discord message that carries visual data."""

    message_id: str
    context_kind: str
    reference_strength: str = ""
    provenance_class: str = ""
    author_user_id: str = ""
    source_turn_message_id: str = ""
    at: str = ""


@dataclass(frozen=True)
class VisionLimits:
    """Per-source count quotas for one request-scoped vision context."""

    attachments: int = 4
    emojis: int = 12
    stickers: int = 8

    @classmethod
    def from_settings(cls, settings) -> VisionLimits:
        return cls(
            attachments=settings.vision_max_attachments,
            emojis=settings.vision_max_emojis,
            stickers=settings.vision_max_stickers,
        )

    def for_source(self, source: str) -> int:
        return {
            "attachment": self.attachments,
            "emoji": self.emojis,
            "sticker": self.stickers,
        }[source]


def _sniff_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


def _emoji_candidates(content: str):
    seen = set()
    for match in _CUSTOM_EMOJI.finditer(content or ""):
        emoji_id = match.group("id")
        if emoji_id in seen:
            continue
        seen.add(emoji_id)
        animated = bool(match.group("animated"))
        extension = "gif" if animated else "png"
        yield (
            f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?size=256&quality=lossless",
            match.group("name"),
        )


def _sticker_candidate(sticker):
    fmt = getattr(getattr(sticker, "format", None), "name", "").lower()
    if fmt == "lottie":
        return None
    url = str(getattr(sticker, "url", "") or "")
    if not url:
        return None
    return url, str(getattr(sticker, "name", "") or "")


def message_has_visual(message, *, include_inline_emojis: bool = True) -> bool:
    """Return whether a Discord message carries a supported visual source without fetching bytes."""
    for attachment in getattr(message, "attachments", ()):
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        if not content_type or content_type.startswith("image/"):
            return True
    if any(
        _sticker_candidate(sticker) is not None
        for sticker in getattr(message, "stickers", ())
    ):
        return True
    return bool(
        include_inline_emojis
        and next(_emoji_candidates(str(getattr(message, "content", "") or "")), None)
    )


def visual_context_refs(rows) -> list[VisualContextRef]:
    """Project selected message context into content-free visual references."""
    result = []
    seen = set()
    for row in rows or ():
        if not row.get("has_visual"):
            continue
        message_id = str(row.get("message_id") or "")
        if not message_id or message_id in seen:
            continue
        seen.add(message_id)
        result.append(VisualContextRef(
            message_id=message_id,
            context_kind=str(row.get("context_kind") or ""),
            reference_strength=str(row.get("reference_strength") or ""),
            provenance_class=str(row.get("provenance_class") or ""),
            author_user_id=str(row.get("author_user_id") or row.get("user_id") or ""),
            source_turn_message_id=str(row.get("source_turn_message_id") or ""),
            at=str(row.get("at") or ""),
        ))
    return result


_VISUAL_CONTEXT_RANK = {
    "replied_message": 0,
    "reply_reference_source": 1,
    "reply_origin_source": 1,
    "reply_origin_request": 1,
    "prior_reply_source": 2,
    "speaker_thread": 3,
    "target_user_history": 4,
    "channel_ambient": 5,
}


def rank_visual_context_refs(rows) -> list[VisualContextRef]:
    """Rank already-admitted visual message refs by provenance before recency."""
    refs = visual_context_refs(rows)

    def key(ref: VisualContextRef):
        rank = _VISUAL_CONTEXT_RANK.get(ref.context_kind, 6)
        try:
            recency = -int(ref.message_id)
        except (TypeError, ValueError):
            recency = 0
        return rank, recency

    return sorted(refs, key=key)


def _message_metadata(message, context_kind: str, reference_strength: str) -> dict[str, str]:
    author = getattr(message, "author", None)
    return {
        "context_kind": context_kind,
        "reference_strength": reference_strength,
        "message_id": str(getattr(message, "id", "") or ""),
        "author_name": str(
            getattr(author, "display_name", getattr(author, "name", "")) or ""
        )[:100],
        "author_user_id": str(getattr(author, "id", "") or ""),
        "message_content": str(getattr(message, "content", "") or "")[:2000],
        "at": (
            getattr(message, "created_at", None).isoformat()
            if getattr(message, "created_at", None) is not None
            else ""
        ),
    }


async def collect_visual_inputs(
    message,
    *,
    context_refs: list[VisualContextRef] | tuple[VisualContextRef, ...] = (),
    limits: VisionLimits | None = None,
    downloader=_download,
    max_context_messages: int = MAX_CONTEXT_VISUAL_MESSAGES,
) -> list[VisualInput]:
    """Fetch visuals only from the current message and already-selected message context."""
    limits = limits or VisionLimits()
    result: list[VisualInput] = []
    counts = {"attachment": 0, "emoji": 0, "sticker": 0}
    used = 0
    seen_message_ids: set[str] = set()

    def has_room(source: str) -> bool:
        return counts[source] < limits.for_source(source) and used < MAX_TOTAL_VISUAL_BYTES

    async def add_bytes(data: bytes, source: str, name: str, metadata: dict[str, str]):
        nonlocal used
        if not has_room(source) or not data:
            return
        if len(data) > MAX_VISUAL_BYTES or used + len(data) > MAX_TOTAL_VISUAL_BYTES:
            return
        mime = _sniff_image_mime(data)
        if mime is None:
            return
        result.append(VisualInput(
            data=data,
            mime_type=mime,
            source=source,
            name=name,
            **metadata,
        ))
        counts[source] += 1
        used += len(data)

    async def collect_message(
        target,
        *,
        context_kind: str,
        reference_strength: str,
        include_inline_emojis: bool,
    ) -> int:
        message_id = str(getattr(target, "id", "") or "")
        if message_id and message_id in seen_message_ids:
            return 0
        if message_id:
            seen_message_ids.add(message_id)

        before = len(result)
        metadata = _message_metadata(target, context_kind, reference_strength)
        for attachment in getattr(target, "attachments", ()):
            if not has_room("attachment"):
                break
            size = int(getattr(attachment, "size", 0) or 0)
            if size > MAX_VISUAL_BYTES or used + size > MAX_TOTAL_VISUAL_BYTES:
                continue
            content_type = str(getattr(attachment, "content_type", "") or "").lower()
            filename = str(getattr(attachment, "filename", "") or "")
            if content_type and not content_type.startswith("image/"):
                continue
            try:
                data = await attachment.read()
            except Exception as exc:  # noqa: BLE001 - Discord failures are content-free in logs
                log.warning("Visual attachment read failed (%s)", type(exc).__name__)
                continue
            await add_bytes(data, "attachment", filename, metadata)

        remote = []
        if include_inline_emojis:
            remote.extend((url, "emoji", name) for url, name in _emoji_candidates(
                str(getattr(target, "content", "") or "")
            ))
        for sticker in getattr(target, "stickers", ()):
            candidate = _sticker_candidate(sticker)
            if candidate is not None:
                remote.append((candidate[0], "sticker", candidate[1]))

        for url, source, name in remote:
            if used >= MAX_TOTAL_VISUAL_BYTES:
                break
            if not has_room(source):
                continue
            try:
                data = await downloader(url)
            except Exception as exc:  # noqa: BLE001 - do not log Discord CDN URLs
                log.warning("Visual asset read failed (%s)", type(exc).__name__)
                continue
            await add_bytes(data, source, name, metadata)
        return len(result) - before

    await collect_message(
        message,
        context_kind="current_message",
        reference_strength="current_message",
        include_inline_emojis=True,
    )

    channel = getattr(message, "channel", None)
    fetch_message = getattr(channel, "fetch_message", None)
    accepted_context_messages = 0
    if fetch_message is not None and max_context_messages > 0:
        for ref in context_refs:
            if accepted_context_messages >= max_context_messages:
                break
            if ref.message_id in seen_message_ids:
                continue
            try:
                target = await fetch_message(int(ref.message_id))
            except (
                TypeError,
                ValueError,
                discord.Forbidden,
                discord.NotFound,
                discord.HTTPException,
            ) as exc:
                log.warning("Selected visual lookup failed (%s)", type(exc).__name__)
                continue
            added = await collect_message(
                target,
                context_kind=ref.context_kind,
                reference_strength=ref.reference_strength,
                include_inline_emojis=ref.context_kind == "replied_message",
            )
            if added:
                accepted_context_messages += 1

    return result

