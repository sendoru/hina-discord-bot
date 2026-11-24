"""Budgeted audio input and independent spoken-evidence verification (#548 P2).

An upload is identified with its Discord sender, not its acoustic speaker.
Only independently verified first-person speech may ground an audio-only memory.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from hina_bot.core.pending_audio import MemoryAudio

MEMORY_AUDIO_MAX_FILES = 2
MEMORY_AUDIO_MAX_BYTES = 16 * 1024 * 1024
MEMORY_AUDIO_CONFIDENCE_CAP = 0.75
_SUPPORTED_MIME = frozenset({
    "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/flac", "audio/webm",
})
_FENCE = re.compile(r"^```(?:json)?\\s*(.*?)\\s*```$", re.IGNORECASE | re.DOTALL)


def select_memory_audios(
    audios: Iterable[MemoryAudio], *,
    max_files: int = MEMORY_AUDIO_MAX_FILES,
    max_bytes: int = MEMORY_AUDIO_MAX_BYTES,
) -> list[MemoryAudio]:
    """Choose newest clips within the total byte budget, in chronological order."""
    chosen: list[MemoryAudio] = []
    used = 0
    for audio in reversed(list(audios)):
        size = len(audio.data)
        if len(chosen) >= max_files:
            break
        if not size or size > 12 * 1024 * 1024 or audio.mime_type not in _SUPPORTED_MIME:
            continue
        if used + size > max_bytes:
            continue
        chosen.append(audio)
        used += size
    return list(reversed(chosen))


def audio_message_ids(audios: Iterable[MemoryAudio]) -> frozenset[str]:
    return frozenset(audio.message_id for audio in audios)


def _rows(text: str, key: str) -> list:
    try:
        value = text.strip()
        fence = _FENCE.fullmatch(value)
        parsed = json.loads(fence.group(1) if fence else value)
        rows = parsed.get(key) if isinstance(parsed, dict) else parsed
        return rows if isinstance(rows, list) else []
    except (ValueError, AttributeError, TypeError):
        return []


def verification_candidates(text: str, audios: Iterable[MemoryAudio]) -> list[dict]:
    """Candidates citing audio actually attached to one of their source turns."""
    available = audio_message_ids(audios)
    result = []
    for index, row in enumerate(_rows(text, "items")[:24]):
        if not isinstance(row, dict) or row.get("kind") == "relationship":
            continue
        refs = row.get("audio_refs")
        sources = row.get("source_message_ids")
        if not isinstance(refs, list) or not isinstance(sources, list):
            continue
        if not refs or any(not isinstance(ref, str) for ref in refs):
            continue
        if not sources or any(not isinstance(source, str) for source in sources):
            continue
        matching = set(refs) & set(sources) & available
        if not matching:
            continue
        content = row.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        result.append({
            "index": index,
            "content": content[:600],
            "evidence": str(row.get("evidence") or "")[:120],
            "audio_refs": sorted(matching),
        })
    return result


def verified_audio_evidence(text: str, candidates: list[dict]) -> dict[int, str]:
    """Fail closed: require explicit keep and a short nonempty spoken quote."""
    allowed = {row["index"] for row in candidates}
    result: dict[int, str] = {}
    seen = set()
    for row in _rows(text, "results"):
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        if type(index) is not int or index not in allowed or index in seen:
            continue
        seen.add(index)
        quote = row.get("evidence")
        if row.get("verdict") != "keep" or not isinstance(quote, str):
            continue
        quote = quote.strip()
        if not quote or len(quote) > 120:
            continue
        result[index] = quote
    return result
