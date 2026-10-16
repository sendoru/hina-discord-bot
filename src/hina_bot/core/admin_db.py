import sqlite3
from contextlib import contextmanager
from pathlib import Path


class AdminDatabase:
    """Own the existing admin connection, shared by instruction/knowledge registries.

    LLM (or the standalone migration caller) creates and closes this database.
    Registries borrow it and use transaction() for standalone writes or to join
    an enclosing transaction() such as a legacy import. This does not join a
    Store transaction or an arbitrary externally issued BEGIN. Constructor DDL
    and the separate connection are preserved; initialization changes are #397.
    """

    def __init__(self, path: str):
        if not path:
            raise ValueError("DATABASE_PATH가 필요합니다.")
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self._transaction_depth = 0
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS instructions (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS runtime_knowledge (
                id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('world_fact','interpretation')),
                content TEXT NOT NULL,
                keywords TEXT NOT NULL,
                subjects TEXT NOT NULL,
                awareness TEXT NOT NULL,
                timeline TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY(kind,id)
            );
            CREATE INDEX IF NOT EXISTS runtime_knowledge_enabled_kind
                ON runtime_knowledge(enabled,kind);
            CREATE TABLE IF NOT EXISTS admin_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

    @contextmanager
    def transaction(self):
        """Nestable transaction; inner registry calls never commit an outer ingest."""
        outer = self._transaction_depth == 0
        if outer:
            self.db.execute("BEGIN IMMEDIATE")
        self._transaction_depth += 1
        try:
            yield
        except Exception:
            self._transaction_depth -= 1
            if outer:
                self.db.rollback()
            raise
        else:
            self._transaction_depth -= 1
            if outer:
                self.db.commit()

    def close(self):
        self.db.close()
