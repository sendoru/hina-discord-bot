"""Existing offline maintenance connection and small inspection queries.

connect_existing transfers ownership to its caller, which closes the connection
and controls BEGIN/commit/rollback. It does not construct Store or run migrations.
Plan callers explicitly use readonly=True: no DDL, secure_delete or checkpoint.
Execution callers retain their existing writer PRAGMAs and transaction boundaries.
Query helpers borrow the connection and never commit or close it. Table names
come only from maintenance code's fixed table lists, never from CLI input.
"""

import sqlite3
from pathlib import Path

from hina_bot.core.db_connections import open_existing, open_readonly


def table_exists(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def row_count(db: sqlite3.Connection, table: str) -> int:
    if not table_exists(db, table):
        return 0
    return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def connect_existing(database_path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")
    db = open_readonly(path) if readonly else open_existing(path)
    try:
        db.execute("PRAGMA busy_timeout=1000")
        if not readonly:
            db.execute("PRAGMA secure_delete=ON")
        if not table_exists(db, "turns"):
            raise ValueError(f"Not a hina runtime database (missing turns table): {path}")
        return db
    except BaseException:
        db.close()
        raise
