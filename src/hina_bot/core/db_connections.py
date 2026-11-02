"""SQLite opens with explicit existence/access contracts, without initialization."""

import sqlite3
from pathlib import Path


class DatabaseMissingError(FileNotFoundError):
    """The SQLite file does not exist; only ``hina-prepare-db`` creates a new one."""


def _connect(path: str | Path, mode: str) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    try:
        return sqlite3.connect(resolved.as_uri() + f"?mode={mode}", uri=True)
    except sqlite3.OperationalError as exc:
        # SQLite only says "unable to open database file"; name the fix when the file is absent.
        # Never create it here: a wrong DATABASE_PATH or unmounted volume would start empty.
        if resolved.exists():
            raise
        raise DatabaseMissingError(
            f"Database file not found: {resolved}. Create a new database with "
            "`hina-prepare-db --yes --offline-confirmed`; if it should already exist, "
            "check DATABASE_PATH and the volume mount."
        ) from exc


def open_readonly(path: str | Path) -> sqlite3.Connection:
    db = _connect(path, "ro")
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db
    except BaseException:
        db.close()
        raise


def open_existing(path: str | Path) -> sqlite3.Connection:
    db = _connect(path, "rw")
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=5000")
        return db
    except BaseException:
        db.close()
        raise
