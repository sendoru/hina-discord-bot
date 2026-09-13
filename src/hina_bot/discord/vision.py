"""Collect bounded image inputs from the current Discord message only."""

from __future__ import annotations

import logging
import re

import httpx

from hina_bot.ai.vision import VisualInput

log = logging.getLogger("hina")

MAX_VISUAL_INPUTS = 4
MAX_VISUAL_BYTES = 5 * 1024 * 1024
MAX_TOTAL_VISUAL_BYTES = 12 * 1024 * 1024
_CUSTOM_EMOJI = re.compile(r"<(?P<animated>a?):(?P<name>[^:<>\s]{1,32}):(?P<id>[0-9]{1,20})>")


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


async def collect_visual_inputs(message, *, downloader=_download) -> list[VisualInput]:
    """Return up to four raster images without retaining them after this turn."""
    result: list[VisualInput] = []
    used = 0

    async def add_bytes(data: bytes, source: str, name: str):
        nonlocal used
        if len(result) >= MAX_VISUAL_INPUTS or not data:
            return
        if len(data) > MAX_VISUAL_BYTES or used + len(data) > MAX_TOTAL_VISUAL_BYTES:
            return
        mime = _sniff_image_mime(data)
        if mime is None:
            return
        result.append(VisualInput(data=data, mime_type=mime, source=source, name=name))
        used += len(data)

    for attachment in getattr(message, "attachments", ()):
        if len(result) >= MAX_VISUAL_INPUTS:
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
        await add_bytes(data, "attachment", filename)

    remote = []
    remote.extend((url, "emoji", name) for url, name in _emoji_candidates(
        str(getattr(message, "content", "") or "")
    ))
    for sticker in getattr(message, "stickers", ()):
        candidate = _sticker_candidate(sticker)
        if candidate is not None:
            remote.append((candidate[0], "sticker", candidate[1]))

    for url, source, name in remote:
        if len(result) >= MAX_VISUAL_INPUTS or used >= MAX_TOTAL_VISUAL_BYTES:
            break
        try:
            data = await downloader(url)
        except Exception as exc:  # noqa: BLE001 - do not log Discord CDN URLs
            log.warning("Visual asset read failed (%s)", type(exc).__name__)
            continue
        await add_bytes(data, source, name)

    return result
