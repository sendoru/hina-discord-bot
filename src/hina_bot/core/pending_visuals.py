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
import logging
import os
import re
import shutil
import stat
import time
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from .image_mime import sniff_image_mime

log = logging.getLogger("hina")

PENDING_VISUAL_DIRNAME = "visual_pending"
_MIB = 1024 * 1024
DEFAULT_MAX_BYTES = 256 * _MIB
DEFAULT_MAX_FILE_BYTES = 5 * _MIB
DEFAULT_MAX_TURN_IMAGES = 2
DEFAULT_MAX_USER_IMAGES = 8
DEFAULT_MAX_USER_BYTES = 24 * _MIB
# A half-written file left by a crash is removed once it is clearly abandoned.
_STALE_TEMP_SECONDS = 60

_EXT_BY_MIME = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
}
_MIME_BY_EXT = {ext: mime for mime, ext in _EXT_BY_MIME.items()}
_REALM = re.compile(r"(guild|dm):(\d{1,20})")
_REALM_DIR = re.compile(r"(guild|dm)-(\d{1,20})")
_DIGITS = re.compile(r"\d{1,20}")
_FILE = re.compile(r"(\d{1,20})\.(\d{1,3})\.(png|jpg|gif|webp)")


def pending_visual_root(database_path: str | Path) -> Path:
    """Where a database's pending photos live, so offline tools reach the same files."""
    return Path(database_path).expanduser().parent / PENDING_VISUAL_DIRNAME


def discard_all_pending_visuals(database_path: str | Path) -> int:
    """For offline tools: delete every pending photo after the turns that cited them are gone.

    The database change is already committed, so a file error is logged (content-free) and
    the next bot start sweeps what is left.
    """
    try:
        return DiskPendingVisualStore(pending_visual_root(database_path)).discard_all()
    except OSError as exc:
        log.warning("Pending visual cleanup failed (%s)", type(exc).__name__)
        return 0


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


@dataclass(frozen=True)
class _Entry:
    path: Path
    realm: str
    channel_id: str
    user_id: str
    message_id: str
    size: int
    mtime: float

    @property
    def conversation(self) -> tuple[str, str, str]:
        return self.realm, self.channel_id, self.user_id

    @property
    def turn(self) -> tuple[str, str, str, str]:
        return self.realm, self.channel_id, self.user_id, self.message_id


def _realm_dir(realm: str) -> str | None:
    match = _REALM.fullmatch(str(realm))
    return f"{match.group(1)}-{match.group(2)}" if match else None


def _subdirs(path: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as listing:
            return [entry for entry in listing if entry.is_dir(follow_symlinks=False)]
    except FileNotFoundError:
        return []


class DiskPendingVisualStore(PendingVisualStore):
    """Photos as files under ``<database folder>/visual_pending``.

    Layout: ``{guild|dm}-{id}/{channel_id}/{user_id}/{message_id}.{n}.{ext}``. Only digits
    reach a path, links are never followed, directories are owner-only and a file appears
    atomically. When space runs out the least recently used photos are evicted, because a
    turn without its photo is still extracted from its text.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_turn_images: int = DEFAULT_MAX_TURN_IMAGES,
        max_user_images: int = DEFAULT_MAX_USER_IMAGES,
        max_user_bytes: int = DEFAULT_MAX_USER_BYTES,
    ):
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.max_file_bytes = max_file_bytes
        self.max_turn_images = max_turn_images
        self.max_user_images = max_user_images
        self.max_user_bytes = max_user_bytes
        self.evicted = 0

    def configure(self, *, max_bytes: int | None = None) -> None:
        if max_bytes is not None:
            self.max_bytes = max_bytes

    # -- layout ---------------------------------------------------------------------

    def _conversation_dir(self, realm, channel_id, user_id) -> Path | None:
        realm_dir = _realm_dir(realm)
        if realm_dir is None or not _DIGITS.fullmatch(str(channel_id)):
            return None
        if not _DIGITS.fullmatch(str(user_id)):
            return None
        return self.root / realm_dir / str(channel_id) / str(user_id)

    def _scan(self) -> tuple[list[_Entry], list[tuple[Path, float]]]:
        """Every well-formed photo file, plus abandoned temp files."""
        entries: list[_Entry] = []
        temps: list[tuple[Path, float]] = []
        for realm_entry in _subdirs(self.root):
            match = _REALM_DIR.fullmatch(realm_entry.name)
            if match is None:
                continue
            realm = f"{match.group(1)}:{match.group(2)}"
            for channel_entry in _subdirs(Path(realm_entry.path)):
                if not _DIGITS.fullmatch(channel_entry.name):
                    continue
                for user_entry in _subdirs(Path(channel_entry.path)):
                    if not _DIGITS.fullmatch(user_entry.name):
                        continue
                    with os.scandir(user_entry.path) as listing:
                        for item in listing:
                            if not item.is_file(follow_symlinks=False):
                                continue
                            info = item.stat(follow_symlinks=False)
                            if item.name.startswith(".") and item.name.endswith(".tmp"):
                                temps.append((Path(item.path), info.st_mtime))
                                continue
                            file_match = _FILE.fullmatch(item.name)
                            if file_match is None:
                                continue
                            entries.append(_Entry(
                                Path(item.path), realm, channel_entry.name, user_entry.name,
                                file_match.group(1), info.st_size, info.st_mtime,
                            ))
        return entries, temps

    def _prune(self) -> None:
        """Remove empty folders below the root, leaving the root itself."""
        for directory, _, _ in os.walk(self.root, topdown=False):
            if Path(directory) != self.root:
                try:
                    os.rmdir(directory)
                except OSError:
                    pass

    def _unlink(self, entries: Iterable[_Entry]) -> int:
        removed = 0
        for entry in entries:
            try:
                entry.path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
        return removed

    def _remove_tree(self, path: Path) -> int:
        if path.is_symlink():
            path.unlink()
            return 0
        if not path.is_dir():
            return 0
        count = sum(
            1 for _, _, names in os.walk(path) for name in names
            if _FILE.fullmatch(name)
        )
        shutil.rmtree(path)
        return count

    def _ensure_dir(self, path: Path) -> None:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700)
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("pending visual folder is not a plain directory")

    # -- interface ------------------------------------------------------------------

    def put(self, realm, channel_id, user_id, message_id, visuals) -> int:
        directory = self._conversation_dir(realm, channel_id, user_id)
        if directory is None or not _DIGITS.fullmatch(str(message_id)):
            return 0
        message_id = str(message_id)
        items = []
        for visual in visuals:
            extension = _EXT_BY_MIME.get(visual.mime_type)
            if (
                extension is None
                or not visual.data
                or len(visual.data) > self.max_file_bytes
                or sniff_image_mime(visual.data) != visual.mime_type
            ):
                continue
            items.append((visual, extension))
            if len(items) >= self.max_turn_images:
                break
        needed = sum(len(visual.data) for visual, _ in items)
        if not items or needed > self.max_bytes:
            return 0

        key = (str(realm), str(channel_id), str(user_id))
        entries, _ = self._scan()
        # A repeated put replaces the earlier photos of the same turn.
        self._unlink(e for e in entries if e.conversation == key and e.message_id == message_id)
        entries = [e for e in entries if not (e.conversation == key and e.message_id == message_id)]
        self._make_room(entries, key, len(items), needed)

        for part in (self.root, directory.parent.parent, directory.parent, directory):
            self._ensure_dir(part)
        written: list[Path] = []
        try:
            for number, (visual, extension) in enumerate(items, 1):
                final = directory / f"{message_id}.{number}.{extension}"
                temp = directory / f".{message_id}.{number}.{extension}.tmp"
                temp.unlink(missing_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(temp, flags, 0o600)
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(visual.data)
                    os.replace(temp, final)
                except BaseException:
                    temp.unlink(missing_ok=True)
                    raise
                written.append(final)
        except BaseException:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return len(written)

    def _make_room(self, entries, key, count: int, needed: int) -> None:
        """Evict least recently used photos until this turn fits the user and total limits."""
        user = [e for e in entries if (e.realm, e.user_id) == (key[0], key[2])]
        user.sort(key=lambda e: (e.mtime, str(e.path)))
        while user and (
            len(user) + count > self.max_user_images
            or sum(e.size for e in user) + needed > self.max_user_bytes
        ):
            oldest = user.pop(0)
            entries.remove(oldest)
            self.evicted += self._unlink([oldest])
        everyone = sorted(entries, key=lambda e: (e.mtime, str(e.path)))
        total = sum(e.size for e in everyone)
        while everyone and total + needed > self.max_bytes:
            oldest = everyone.pop(0)
            total -= oldest.size
            self.evicted += self._unlink([oldest])
        self._prune()

    def get(self, realm, channel_id, user_id, message_ids):
        directory = self._conversation_dir(realm, channel_id, user_id)
        wanted = [str(value) for value in message_ids if _DIGITS.fullmatch(str(value))]
        if directory is None or not wanted:
            return {}
        found: dict[str, list[tuple[int, MemoryVisual]]] = {}
        try:
            listing = list(os.scandir(directory))
        except FileNotFoundError:
            return {}
        for item in listing:
            match = _FILE.fullmatch(item.name)
            if match is None or match.group(1) not in wanted:
                continue
            if not item.is_file(follow_symlinks=False):
                continue
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(item.path, flags)
                with os.fdopen(descriptor, "rb") as handle:
                    data = handle.read()
            except OSError:
                continue
            mime = _MIME_BY_EXT[match.group(3)]
            if sniff_image_mime(data) != mime:
                continue
            try:
                os.utime(item.path)  # recently used: the last to be evicted
            except OSError:
                pass
            number = int(match.group(2))
            found.setdefault(match.group(1), []).append(
                (number, MemoryVisual(match.group(1), number, mime, data))
            )
        return {
            message_id: [visual for _, visual in sorted(found[message_id], key=lambda p: p[0])]
            for message_id in wanted if message_id in found
        }

    def discard_messages(self, realm, channel_id, user_id, message_ids) -> int:
        directory = self._conversation_dir(realm, channel_id, user_id)
        wanted = {str(value) for value in message_ids if _DIGITS.fullmatch(str(value))}
        if directory is None or not wanted:
            return 0
        key = (str(realm), str(channel_id), str(user_id))
        entries, _ = self._scan()
        removed = self._unlink(e for e in entries if e.conversation == key and e.message_id in wanted)
        self._prune()
        return removed

    def discard_user(self, realm, user_id) -> int:
        realm_dir = _realm_dir(realm)
        if realm_dir is None or not _DIGITS.fullmatch(str(user_id)):
            return 0
        removed = 0
        for channel_entry in _subdirs(self.root / realm_dir):
            removed += self._remove_tree(Path(channel_entry.path) / str(user_id))
        self._prune()
        return removed

    def discard_channel(self, realm, channel_id) -> int:
        realm_dir = _realm_dir(realm)
        if realm_dir is None or not _DIGITS.fullmatch(str(channel_id)):
            return 0
        removed = self._remove_tree(self.root / realm_dir / str(channel_id))
        self._prune()
        return removed

    def discard_realm(self, realm) -> int:
        realm_dir = _realm_dir(realm)
        return self._remove_tree(self.root / realm_dir) if realm_dir else 0

    def discard_all(self) -> int:
        removed = 0
        try:
            children = list(os.scandir(self.root))
        except FileNotFoundError:
            return 0
        for child in children:
            removed += self._remove_tree(Path(child.path))
        return removed

    def sweep(self, valid, *, max_age_seconds, now=None) -> int:
        current = time.time() if now is None else now
        pending = {tuple(str(part) for part in key) for key in valid}
        entries, temps = self._scan()
        removed = self._unlink(
            e for e in entries
            if e.turn not in pending or current - e.mtime > max_age_seconds
        )
        for path, modified in temps:
            if current - modified > _STALE_TEMP_SECONDS:
                path.unlink(missing_ok=True)
        self._prune()
        return removed

    def stats(self) -> tuple[int, int]:
        """``(files, bytes)`` currently held; for tests and diagnostics."""
        entries, _ = self._scan()
        return len(entries), sum(e.size for e in entries)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "PENDING_VISUAL_DIRNAME",
    "DiskPendingVisualStore",
    "MemoryVisual",
    "NullPendingVisualStore",
    "PendingVisualStore",
    "discard_all_pending_visuals",
    "pending_visual_root",
]
