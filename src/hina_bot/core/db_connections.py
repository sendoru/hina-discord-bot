"""SQLite opens with explicit existence/access contracts, without initialization."""

import sqlite3
from pathlib import Path


def open_readonly(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(path).expanduser().resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db
    except BaseException:
        db.close()
        raise


def open_existing(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(path).expanduser().resolve().as_uri() + "?mode=rw", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=5000")
        return db
    except BaseException:
        db.close()
        raise
