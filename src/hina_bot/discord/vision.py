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
RECENT_VISUAL_SCAN_LIMIT = 12
MAX_RECENT_VISUAL_MESSAGES = 3
_CUSTOM_EMOJI = re.compile(r"<(?P<animated>a?):(?P<name>[^:<>\s]{1,32}):(?P<id>[0-9]{1,20})>")


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
    }


async def _resolve_reply_message(message):
    reference = getattr(message, "reference", None)
    if reference is None:
        return None

    channel = getattr(message, "channel", None)
    current_channel_id = getattr(channel, "id", None)
    reference_channel_id = getattr(reference, "channel_id", None)
    if (
        current_channel_id is not None
        and reference_channel_id is not None
        and current_channel_id != reference_channel_id
    ):
        return None

    target = getattr(reference, "resolved", None)
    if target is not None and getattr(target, "author", None) is None:
        target = None
    if target is None:
        message_id = getattr(reference, "message_id", None)
        fetch_message = getattr(channel, "fetch_message", None)
        if message_id is None or fetch_message is None:
            return None
        try:
            target = await fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            log.warning("Reply visual lookup failed (%s)", type(exc).__name__)
            return None

    target_channel = getattr(target, "channel", None)
    target_channel_id = getattr(target_channel, "id", current_channel_id)
    if (
        current_channel_id is not None
        and target_channel_id is not None
        and current_channel_id != target_channel_id
    ):
        return None
    return target


def _has_passive_visual_candidate(message) -> bool:
    for attachment in getattr(message, "attachments", ()):
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        if not content_type or content_type.startswith("image/"):
            return True
    return any(
        _sticker_candidate(sticker) is not None
        for sticker in getattr(message, "stickers", ())
    )


async def collect_visual_inputs(
    message,
    *,
    limits: VisionLimits | None = None,
    downloader=_download,
    include_reply: bool = False,
    include_recent: bool = False,
    allowed_reply_author_id: int | None = None,
    allowed_context_author_id: int | None = None,
    recent_filter=None,
    recent_scan_limit: int = RECENT_VISUAL_SCAN_LIMIT,
    recent_message_limit: int = MAX_RECENT_VISUAL_MESSAGES,
) -> list[VisualInput]:
    """Return bounded request-scoped visual context without retaining image bytes."""
    if allowed_reply_author_id is None:
        allowed_reply_author_id = allowed_context_author_id
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

    if include_reply:
        target = await _resolve_reply_message(message)
        target_author_id = getattr(getattr(target, "author", None), "id", None)
        if (
            target is not None
            and (
                allowed_reply_author_id is None
                or target_author_id == allowed_reply_author_id
            )
        ):
            await collect_message(
                target,
                context_kind="replied_message",
                reference_strength="explicit_reply",
                include_inline_emojis=True,
            )

    if include_recent and recent_scan_limit > 0 and recent_message_limit > 0:
        channel = getattr(message, "channel", None)
        history = getattr(channel, "history", None)
        if history is not None:
            recent_with_visuals = 0
            try:
                async for old in history(
                    limit=max(1, int(recent_scan_limit)),
                    before=message,
                    oldest_first=False,
                ):
                    old_id = str(getattr(old, "id", "") or "")
                    if old_id and old_id in seen_message_ids:
                        continue
                    if recent_filter is not None and not recent_filter(old):
                        continue
                    if not _has_passive_visual_candidate(old):
                        continue
                    added = await collect_message(
                        old,
                        context_kind="recent_channel_message",
                        reference_strength="passive_recent",
                        include_inline_emojis=False,
                    )
                    if added:
                        recent_with_visuals += 1
                    if recent_with_visuals >= recent_message_limit:
                        break
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Recent visual history lookup failed (%s)", type(exc).__name__)

    return result
