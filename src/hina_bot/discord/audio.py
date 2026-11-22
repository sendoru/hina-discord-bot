"""Bounded Discord audio attachment collection for native Gemini understanding."""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import wave
from dataclasses import dataclass
from pathlib import PurePath

import discord

from hina_bot.ai.audio import AudioInput

from .vision import VisualContextRef, _message_metadata

log = logging.getLogger("hina")

AUDIO_MAX_BYTES = 12 * 1024 * 1024
AUDIO_TOTAL_BYTES = 16 * 1024 * 1024
_AUDIO_SUFFIXES = frozenset({".ogg", ".oga", ".opus", ".mp3", ".m4a",
                             ".mp4", ".wav", ".flac", ".webm"})
_MIME_BY_KIND = {
    "ogg": "audio/ogg",
    "mp3": "audio/mpeg",
    "mp4": "audio/mp4",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "webm": "audio/webm",
}


@dataclass(frozen=True)
class AudioLimits:
    attachments: int = 2
    max_seconds: float = 300

    @classmethod
    def from_settings(cls, settings):
        return cls(
            attachments=settings.audio_max_attachments,
            max_seconds=settings.audio_max_seconds,
        )


def audio_attachments(message):
    for attachment in getattr(message, "attachments", ()) or ():
        filename = str(getattr(attachment, "filename", "") or "")
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        voice_flag = getattr(getattr(message, "flags", None), "voice", False)
        if (voice_flag or content_type.startswith("audio/")
                or content_type == "application/ogg"
                or PurePath(filename.lower()).suffix in _AUDIO_SUFFIXES):
            yield attachment


def message_has_audio(message) -> bool:
    return next(audio_attachments(message), None) is not None


def audio_context_text(message) -> str:
    """Metadata only: no transcription, raw URL, or content inferred from an audio file."""
    items = []
    for item in list(audio_attachments(message))[:4]:
        name = str(getattr(item, "filename", "") or "")[:100]
        duration = getattr(item, "duration_secs", None)
        duration_text = (
            f"{float(duration):.1f}초" if isinstance(duration, (int, float))
            and 0 < duration < 24 * 3600 else "미확인"
        )
        items.append(f"[첨부 오디오: {name}; 재생 길이 {duration_text}; 원본은 별도 입력]")
    return "\n".join(items)


def audio_context_refs(rows) -> list[VisualContextRef]:
    """Use only provenance-selected messages, ordered by strength then recency."""
    refs = []
    seen = set()
    # Only explicit causal reply links may bring original audio into an answer.
    # Ordinary recent messages (even the same speaker) are metadata-only: audio
    # otherwise disables Gemini code execution on otherwise unrelated turns.
    allowed = {
        "replied_message": "explicit_reply",
        "reply_origin_request": "prior_user_request",
        "reply_origin_source": "prior_explicit_reply",
    }
    order = {name: i for i, name in enumerate(allowed)}

    for row in rows or ():
        mid = str(row.get("message_id") or "")
        kind = str(row.get("context_kind") or "")
        strength = str(row.get("reference_strength") or "")
        if not mid or not row.get("has_audio") or mid in seen:
            continue
        if allowed.get(kind) != strength:
            continue
        # Origin sources can include passively selected images/other references.
        # An audio source must actually have been a *direct reply*, not merely
        # inherited as a reference of the replied-to assistant answer.
        if kind == "reply_origin_source" and not row.get("audio_explicit_reference"):
            continue
        seen.add(mid)
        refs.append(VisualContextRef(
            message_id=mid,
            context_kind=str(row.get("context_kind") or ""),
            reference_strength=str(row.get("reference_strength") or ""),
            author_user_id=str(row.get("author_user_id") or row.get("user_id") or ""),
        ))
    return sorted(
        refs,
        key=lambda r: (order.get(r.context_kind, 6), -int(r.message_id)
                       if r.message_id.isdecimal() else 0),
    )


def _sniff_audio(data: bytes) -> str | None:
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if data[4:8] == b"ftyp" and data[8:12] not in {b"heic", b"avif"}:
        return "audio/mp4"
    if data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    return None


def _mp3_duration(data: bytes) -> float | None:
    offset = 0
    if data.startswith(b"ID3") and len(data) >= 10:
        size = 0
        for byte in data[6:10]:
            size = (size << 7) | (byte & 0x7F)
        offset = min(len(data), 10 + size)
    frames = 0
    seconds = 0.0
    while offset + 4 <= len(data):
        h = int.from_bytes(data[offset:offset + 4], "big")
        version = (h >> 19) & 3
        layer = (h >> 17) & 3
        bitrate_idx = (h >> 12) & 15
        sample_idx = (h >> 10) & 3
        if ((h >> 21) != 0x7FF or version == 1 or layer != 1
                or bitrate_idx in {0, 15} or sample_idx == 3):
            if frames:
                break
            offset += 1
            continue
        rates = (44100, 48000, 32000)
        sample_rate = rates[sample_idx] // (1 if version == 3 else 2 if version == 2 else 4)
        bitrates = (
            (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
            (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
        )
        bitrate = bitrates[0 if version == 3 else 1][bitrate_idx]
        samples = 1152 if version == 3 else 576
        frame_size = (144000 if version == 3 else 72000) * bitrate // sample_rate
        frame_size += (h >> 9) & 1
        if frame_size < 4 or offset + frame_size > len(data):
            break
        seconds += samples / sample_rate
        frames += 1
        offset += frame_size
    return seconds if frames > 0 else None


def _audio_duration(data: bytes, mime: str) -> float | None:
    """Read duration from audio container, without running decoders or external tools."""
    try:
        if mime == "audio/wav":
            with wave.open(io.BytesIO(data), "rb") as stream:
                return stream.getnframes() / stream.getframerate()
        if mime == "audio/mpeg":
            return _mp3_duration(data)
        if mime == "audio/flac" and len(data) >= 26:
            streaminfo = int.from_bytes(data[18:26], "big")
            rate = (streaminfo >> 44) & 0xFFFFF
            samples = streaminfo & ((1 << 36) - 1)
            return samples / rate if rate else None
        if mime == "audio/mp4":
            pos = data.find(b"mvhd")
            if pos != -1 and pos + 36 <= len(data):
                version = data[pos + 4]
                if version == 0:
                    scale = int.from_bytes(data[pos + 16:pos + 20], "big")
                    ticks = int.from_bytes(data[pos + 20:pos + 24], "big")
                else:
                    scale = int.from_bytes(data[pos + 24:pos + 28], "big")
                    ticks = int.from_bytes(data[pos + 28:pos + 36], "big")
                return ticks / scale if scale else None
        if mime == "audio/ogg":
            last = data.rfind(b"OggS")
            if last >= 0 and len(data) >= last + 14:
                granule = struct.unpack_from("<Q", data, last + 6)[0]
                if b"OpusHead" in data[:256]:
                    return granule / 48000
                idx = data.find(b"\x01vorbis")
                if idx >= 0 and idx + 16 <= len(data):
                    rate = int.from_bytes(data[idx + 12:idx + 16], "little")
                    return granule / rate if rate else None
    except (ValueError, EOFError, wave.Error, ZeroDivisionError, struct.error):
        return None
    return None


async def collect_audio_inputs(message, *, context_refs=(), limits=None):
    """Return only budgeted, authorized-by-selection current/reply/recent media."""
    limits = limits or AudioLimits()
    if limits.attachments <= 0:
        return []
    result = []
    bytes_used = 0
    seen = set()

    async def collect(target, kind, strength):
        nonlocal bytes_used
        mid = str(getattr(target, "id", "") or "")
        if mid in seen:
            return
        seen.add(mid)
        metadata = _message_metadata(target, kind, strength)
        for attachment in audio_attachments(target):
            if len(result) >= limits.attachments:
                break
            size = int(getattr(attachment, "size", 0) or 0)
            if size <= 0 or size > AUDIO_MAX_BYTES or bytes_used + size > AUDIO_TOTAL_BYTES:
                continue
            declared = getattr(attachment, "duration_secs", None)
            if isinstance(declared, (int, float)) and declared > limits.max_seconds:
                continue
            try:
                data = await asyncio.wait_for(attachment.read(), timeout=15)
            except (discord.HTTPException, TimeoutError, OSError) as exc:
                log.warning("Audio attachment read failed (%s)", type(exc).__name__)
                continue
            if not data or len(data) > AUDIO_MAX_BYTES or bytes_used + len(data) > AUDIO_TOTAL_BYTES:
                continue
            mime = _sniff_audio(data)
            if mime is None:
                continue
            duration = _audio_duration(data, mime)
            if duration is None and isinstance(declared, (int, float)):
                duration = float(declared)
            # Duration must be known to enforce a sound token budget, not inferred
            # from compressed file bytes. Unsupported containers remain metadata-only.
            if duration is None or not 0 < duration <= limits.max_seconds:
                continue
            result.append(AudioInput(
                data=data,
                mime_type=mime,
                source="attachment",
                duration_seconds=duration,
                name=str(getattr(attachment, "filename", "") or "")[:100],
                **metadata,
            ))
            bytes_used += len(data)

    await collect(message, "current_message", "current_message")
    fetch = getattr(getattr(message, "channel", None), "fetch_message", None)
    if callable(fetch):
        for ref in context_refs:
            if len(result) >= limits.attachments:
                break
            if ref.message_id in seen:
                continue
            try:
                target = await asyncio.wait_for(fetch(int(ref.message_id)), timeout=10)
            except (discord.HTTPException, ValueError, TimeoutError, TypeError) as exc:
                log.warning("Audio context lookup failed (%s)", type(exc).__name__)
                continue
            await collect(target, ref.context_kind, ref.reference_strength)
    return result
