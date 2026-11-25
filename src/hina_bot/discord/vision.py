"""Collect bounded image inputs from current, replied-to, and recent Discord messages."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

import discord
import httpx
from PIL import Image, ImageOps

from hina_bot.ai.vision import VisualInput
from hina_bot.core.image_mime import sniff_image_mime
from hina_bot.core.pending_visuals import MemoryVisual

log = logging.getLogger("hina")

MAX_VISUAL_BYTES = 5 * 1024 * 1024
MAX_TOTAL_VISUAL_BYTES = 12 * 1024 * 1024
MAX_SOURCE_VISUAL_BYTES = 32 * 1024 * 1024
MAX_DECODE_PIXELS = 32_000_000
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
    embeds: int = 2
    max_image_bytes: int = MAX_VISUAL_BYTES
    max_total_bytes: int = MAX_TOTAL_VISUAL_BYTES

    @classmethod
    def from_settings(cls, settings) -> VisionLimits:
        return cls(
            attachments=settings.vision_max_attachments,
            emojis=settings.vision_max_emojis,
            stickers=settings.vision_max_stickers,
            max_image_bytes=settings.vision_max_image_mb * 1024 * 1024,
            max_total_bytes=settings.vision_max_total_mb * 1024 * 1024,
        )

    def for_source(self, source: str) -> int:
        return {
            "attachment": self.attachments,
            "emoji": self.emojis,
            "sticker": self.stickers,
            "embed": self.embeds,
        }[source]


class VisionFetchCache:
    """Small per-client TTL/LRU cache for fetched *historical* visuals.

    Cache bytes and immutable source metadata, never an authorization decision or
    request-specific provenance. Each request independently selects its allowed
    refs; cached bytes are relabeled with that request's context strength.
    """

    def __init__(self, *, ttl_seconds=300, empty_ttl_seconds=15,
                 max_entries=48, max_bytes=16 * 1024 * 1024):
        self.ttl_seconds = ttl_seconds
        self.empty_ttl_seconds = empty_ttl_seconds
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.total_bytes = 0

    def _remove(self, key):
        entry = self.entries.pop(key, None)
        if entry is not None:
            self.total_bytes -= entry[2]

    def get(self, key):
        entry = self.entries.get(key)
        if entry is None:
            return None
        added_at, visuals, _size = entry
        ttl = self.ttl_seconds if visuals else self.empty_ttl_seconds
        if time.monotonic() - added_at >= ttl:
            self._remove(key)
            return None
        self.entries.move_to_end(key)
        return visuals

    def invalidate(self, channel_id, message_id):
        for key in list(self.entries):
            if key[0] == channel_id and key[1] == str(message_id):
                self._remove(key)

    def put(self, key, visuals):
        visuals = tuple(visuals)
        size = sum(len(item.data) for item in visuals)
        self._remove(key)
        if size > self.max_bytes or self.max_entries <= 0:
            return
        self.entries[key] = (time.monotonic(), visuals, size)
        self.total_bytes += size
        while len(self.entries) > self.max_entries or self.total_bytes > self.max_bytes:
            self._remove(next(iter(self.entries)))


_sniff_image_mime = sniff_image_mime


async def _download(url: str) -> bytes:
    async with (
        httpx.AsyncClient(timeout=15, follow_redirects=True) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        return await _bounded_body(response)


async def _bounded_body(response) -> bytes:
    size = response.headers.get("content-length")
    if size:
        try:
            if int(size) > MAX_SOURCE_VISUAL_BYTES:
                log.warning("Visual source skipped: reason=source_size_limit")
                return b""
        except ValueError:
            pass
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_SOURCE_VISUAL_BYTES:
            log.warning("Visual source skipped: reason=source_size_limit")
            return b""
        body.extend(chunk)
    return bytes(body)


def fit_visual_bytes(data: bytes, budget: int) -> tuple[bytes, str, str]:
    """Fit a static image to the transfer budget; preserve animated originals."""
    if len(data) > MAX_SOURCE_VISUAL_BYTES:
        return b"", "", "source_size_limit"
    mime = _sniff_image_mime(data)
    if mime is None:
        return b"", "", "unsupported_image"
    if len(data) <= budget:
        return data, mime, "original"
    if budget <= 0:
        return b"", "", "total_budget"
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > MAX_DECODE_PIXELS:
                return b"", "", "pixel_limit"
            if getattr(source, "n_frames", 1) > 1:
                return b"", "", "animated_over_budget"
            oriented = ImageOps.exif_transpose(source)
            image = oriented.convert(
                "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info
                else "RGB"
            )
            # WebP has a maximum side length of 16383 pixels.
            if max(image.size) > 16383:
                image.thumbnail((16383, 16383), Image.Resampling.LANCZOS)
            else:
                output = BytesIO()
                image.save(output, "WEBP", lossless=True, method=4, exact=True)
                if output.tell() <= budget:
                    return output.getvalue(), "image/webp", "lossless"
            for _ in range(10):
                for quality in (92, 82, 70):
                    output = BytesIO()
                    image.save(output, "WEBP", quality=quality, method=4)
                    if output.tell() <= budget:
                        return output.getvalue(), "image/webp", "compressed"
                width, height = image.size
                if min(width, height) <= 1 and max(width, height) <= 1:
                    break
                image = image.resize(
                    (max(1, width * 3 // 4), max(1, height * 3 // 4)),
                    Image.Resampling.LANCZOS,
                )
    except (OSError, ValueError, Image.DecompressionBombError):
        return b"", "", "decode_failed"
    return b"", "", "compression_budget"


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


def _safe_discord_proxy_url(url: str) -> str:
    """Allow only Discord-served HTTPS proxies, never arbitrary preview URLs."""
    if not url or len(url) > 2048:
        return ""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        approved = host in {"media.discordapp.net", "cdn.discordapp.com"} or (
            re.fullmatch(r"images-ext-[0-9]+\.discordapp\.net", host) is not None
        )
        if (
            parsed.scheme != "https" or not approved
            or parsed.port not in (None, 443)
            or parsed.username or parsed.password or not parsed.path.startswith("/")
        ):
            return ""
    except ValueError:
        return ""
    return url


def _discord_proxy_url(media) -> str:
    return _safe_discord_proxy_url(str(getattr(media, "proxy_url", "") or ""))


def _embed_candidates(message):
    """Images supplied by Discord embeds: main image first, fallback thumbnail.

    Skip images already attached directly to this same message. Profile icons
    and provider favicons are deliberately never treated as message images.
    """
    attached = {
        str(getattr(item, "url", "") or "")
        for item in (getattr(message, "attachments", None) or ())
    }
    seen = set()
    for embed in (getattr(message, "embeds", None) or ())[:2]:
        for field, name in (("image", "미리보기 이미지"), ("thumbnail", "미리보기 썸네일")):
            media = getattr(embed, field, None)
            if media is None:
                continue
            original = str(getattr(media, "url", "") or "")
            url = _discord_proxy_url(media)
            if (original and original in attached) or (url and url in seen):
                break
            if not url:
                continue
            seen.add(url)
            yield url, name
            break  # One media item per embed, never also a profile-like thumbnail.


async def _download_embed(url: str) -> bytes:
    """Stream only Discord proxy URLs, rejecting redirects and oversized data."""
    if not _safe_discord_proxy_url(url):
        return b""
    async with (
        httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        return await _bounded_body(response)


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
    if next(_embed_candidates(message), None) is not None:
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
    cache: VisionFetchCache | None = None,
    downloader=_download,
    embed_downloader=_download_embed,
    max_context_messages: int = MAX_CONTEXT_VISUAL_MESSAGES,
) -> list[VisualInput]:
    """Fetch visuals only from the current message and already-selected message context."""
    limits = limits or VisionLimits()
    result: list[VisualInput] = []
    counts = {"attachment": 0, "emoji": 0, "sticker": 0, "embed": 0}
    used = 0
    seen_message_ids: set[str] = set()

    def has_room(source: str) -> bool:
        return counts[source] < limits.for_source(source) and used < limits.max_total_bytes

    async def add_bytes(
        data: bytes,
        source: str,
        name: str,
        metadata: dict[str, str],
        *,
        uri: str = "",
    ):
        nonlocal used
        if not has_room(source):
            log.info("Visual skipped: source=%s reason=quota_or_total_budget", source)
            return
        if not data:
            log.warning("Visual skipped: source=%s reason=empty_download", source)
            return
        budget = min(limits.max_image_bytes, limits.max_total_bytes - used)
        original_size = len(data)
        if original_size > budget:
            data, mime, reason = await asyncio.to_thread(fit_visual_bytes, data, budget)
        else:
            data, mime, reason = fit_visual_bytes(data, budget)
        if not data:
            log.warning(
                "Visual skipped: source=%s reason=%s bytes=%d budget=%d",
                source, reason, original_size, budget,
            )
            return
        if reason != "original":
            log.info(
                "Visual compressed: source=%s method=%s before_bytes=%d after_bytes=%d",
                source, reason, original_size, len(data),
            )
        result.append(VisualInput(
            data=data,
            mime_type=mime,
            source=source,
            name=name,
            uri=uri,
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
            content_type = str(getattr(attachment, "content_type", "") or "").lower()
            filename = str(getattr(attachment, "filename", "") or "")
            if content_type and not content_type.startswith("image/"):
                continue
            if not has_room("attachment"):
                log.info("Visual skipped: source=attachment reason=quota_or_total_budget")
                break
            size = int(getattr(attachment, "size", 0) or 0)
            if size > MAX_SOURCE_VISUAL_BYTES:
                log.warning("Visual skipped: source=attachment reason=source_size_limit bytes=%d", size)
                continue
            try:
                data = await attachment.read()
            except Exception as exc:  # noqa: BLE001 - Discord failures are content-free in logs
                log.warning("Visual skipped: source=attachment reason=download_failed error=%s",
                            type(exc).__name__)
                continue
            await add_bytes(
                data,
                "attachment",
                filename,
                metadata,
                uri=str(getattr(attachment, "url", "") or ""),
            )

        remote = []
        if include_inline_emojis:
            remote.extend((url, "emoji", name) for url, name in _emoji_candidates(
                str(getattr(target, "content", "") or "")
            ))
        for sticker in getattr(target, "stickers", ()):
            candidate = _sticker_candidate(sticker)
            if candidate is not None:
                remote.append((candidate[0], "sticker", candidate[1]))
        remote.extend((url, "embed", name) for url, name in _embed_candidates(target))

        for url, source, name in remote:
            if not has_room(source):
                log.info("Visual skipped: source=%s reason=quota_or_total_budget", source)
                continue
            try:
                data = await (embed_downloader if source == "embed" else downloader)(url)
            except Exception as exc:  # noqa: BLE001 - do not log Discord CDN URLs
                log.warning("Visual skipped: source=%s reason=download_failed error=%s",
                            source, type(exc).__name__)
                continue
            await add_bytes(data, source, name, metadata, uri=url)
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
            # Inline emojis in an explicit reply are included, but passive inline
            # emojis are not. Keep the two payload selections in separate entries.
            inline_emojis = ref.context_kind == "replied_message"
            # Cache a collection only for the same remaining transfer budget and quotas.
            # Otherwise an already compressed image could outlive a runtime budget increase.
            key = (
                getattr(channel, "id", None), ref.message_id, inline_emojis,
                limits, used, tuple(counts.items()),
            )
            cached = cache.get(key) if cache is not None else None
            if cached is not None:
                seen_message_ids.add(ref.message_id)
                before = len(result)
                for item in cached:
                    await add_bytes(
                        item.data, item.source, item.name,
                        {
                            "context_kind": ref.context_kind,
                            "reference_strength": ref.reference_strength,
                            "message_id": item.message_id,
                            "author_name": item.author_name,
                            "author_user_id": item.author_user_id,
                            "message_content": item.message_content,
                            "at": item.at,
                        },
                        uri=item.uri,
                    )
                if len(result) > before:
                    accepted_context_messages += 1
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
                if cache is not None and isinstance(exc, discord.NotFound):
                    cache.put(key, ())
                continue
            before = len(result)
            added = await collect_message(
                target,
                context_kind=ref.context_kind,
                reference_strength=ref.reference_strength,
                include_inline_emojis=inline_emojis,
            )
            if cache is not None:
                # Empty/no-image refs are common with passive inline emojis.
                # Cache them briefly; do not permanently cache failed downloads.
                collected = result[before:]
                if collected or not message_has_visual(
                    target, include_inline_emojis=inline_emojis
                ):
                    cache.put(key, collected)
            if added:
                accepted_context_messages += 1

    return result


def memory_visuals_for_turn(visuals, *, user_id, message_id) -> list[MemoryVisual]:
    """The photos this user attached to this very message, for later memory extraction.

    Custom emoji, stickers, images from a replied-to or recent message and anything another
    person posted never qualify: the memory is the sender's own, and only their own photo can
    say something about them.
    """

    mine = [
        visual for visual in visuals
        if visual.source == "attachment"
        and visual.context_kind == "current_message"
        and str(visual.author_user_id) == str(user_id)
        and str(visual.message_id) == str(message_id)
    ]
    return [
        MemoryVisual(str(message_id), number, visual.mime_type, visual.data)
        for number, visual in enumerate(mine, 1)
    ]
