"""Verified private SQLite snapshot for offline conversation maintenance.

Snapshots are created before any LLM memory flush or destructive reset.
The source must be quiescent: this helper is not a substitute for stopping writers.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    sha256: str
    bytes: int


def _counts(db: sqlite3.Connection) -> dict[str, int]:
    required = (
        "turns", "shared_calls", "shared_summaries", "memory_items",
        "memory_extraction_cursors",
    )
    counts = {}
    for table in required:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is None:
            raise ValueError(f"Not a Hina runtime SQLite database: missing {table}")
        counts[table] = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


def backup_sqlite(path: str | Path, *, backup_dir: str | Path | None = None) -> BackupInfo:
    """Create and verify a non-overwriting SQLite backup, including WAL frames.

    Caller MUST stop all writers first. Source changes during backup fail the row
    comparison, but independent process quiescence cannot be proved by this function.
    """
    source_path = Path(path)
    if not source_path.is_file() or source_path.is_symlink():
        raise FileNotFoundError(f"Missing or linked SQLite database: {source_path}")

    destination = (
        Path(backup_dir) if backup_dir is not None
        else source_path.parent / "backups"
    )
    if destination.is_symlink():
        raise ValueError("Backup directory must not be a symbolic link")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.stat().st_mode & 0o077:
        raise PermissionError("Backup directory must be owner-only (chmod 700)")
    if destination.resolve() == source_path.parent.resolve():
        raise ValueError("Backups must use a separate directory")

    date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    basename = f"{source_path.stem}-{date}-{uuid.uuid4().hex}.sqlite3"
    final = destination / basename
    temporary = destination / (basename + ".partial")
    source = None
    try:
        handle = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(handle)
        source = sqlite3.connect(str(source_path), timeout=5)
        before = _counts(source)
        snapshot = sqlite3.connect(str(temporary))
        try:
            source.backup(snapshot)
        finally:
            snapshot.close()
        with closing(sqlite3.connect(f"file:{temporary.resolve()}?mode=ro", uri=True)) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite backup integrity_check failed")
            if _counts(check) != before:
                raise RuntimeError("SQLite backup row count mismatch; stop all writers")
        if _counts(source) != before:
            raise RuntimeError("SQLite changed during snapshot; stop all writers")

        digest = hashlib.sha256()
        with temporary.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        # Atomic no-overwrite publish. os.replace would silently overwrite another backup.
        os.link(temporary, final)
        temporary.unlink()
        return BackupInfo(final, digest.hexdigest(), final.stat().st_size)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if source is not None:
            source.close()
