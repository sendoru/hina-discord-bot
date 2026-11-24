"""Opt-in pending native audio files for structured-memory extraction (#548 P1).

The original uploader is not necessarily the speaker. Audio is stored only for an
own, current Discord turn, never used as independent identity evidence. P1 stores
and deletes media but does not yet inject it into the extractor (P2).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .audio_mime import sniff_audio_mime
from .pending_visuals import DiskPendingVisualStore, MemoryVisual, NullPendingVisualStore

log = logging.getLogger("hina")
PENDING_AUDIO_DIRNAME = "audio_pending"
_MIB = 1024 * 1024
_EXT_BY_MIME = {
    "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "mp4",
    "audio/wav": "wav", "audio/flac": "flac", "audio/webm": "webm",
}
_MIME_BY_EXT = {value: key for key, value in _EXT_BY_MIME.items()}
_FILE = re.compile(r"(\\d{1,20})\\.(\\d{1,3})\\.(ogg|mp3|mp4|wav|flac|webm)")


def pending_audio_root(database_path: str | Path) -> Path:
    return Path(database_path).expanduser().parent / PENDING_AUDIO_DIRNAME


@dataclass(frozen=True)
class MemoryAudio(MemoryVisual):
    """Raw bytes of a validated current-message attachment, keyed by source turn."""


class PendingAudioStore(NullPendingVisualStore):
    """No-op pending audio interface for in-memory stores."""


NullPendingAudioStore = PendingAudioStore


class DiskPendingAudioStore(DiskPendingVisualStore, PendingAudioStore):
    """Disk store sharing the visual store's ownership, quotas and safe lifecycle."""

    file_pattern = _FILE
    extensions = _EXT_BY_MIME
    mime_by_ext = _MIME_BY_EXT
    sniff_mime = staticmethod(sniff_audio_mime)
    item_type = MemoryAudio

    def __init__(self, root: str | Path, *, max_bytes: int = 256 * _MIB):
        super().__init__(root, max_bytes=max_bytes, max_file_bytes=12 * _MIB,
                         max_turn_images=2, max_user_images=6, max_user_bytes=48 * _MIB)


def discard_all_pending_audio(database_path: str | Path) -> int:
    try:
        return DiskPendingAudioStore(pending_audio_root(database_path)).discard_all()
    except OSError as exc:
        log.warning("Pending audio cleanup failed (%s)", type(exc).__name__)
        return 0
