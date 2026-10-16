import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .db_connections import open_existing, open_readonly
from .db_schema import prepare_connection, require_current


class AdminDatabase:
    """Own the existing admin connection, shared by instruction/knowledge registries.

    Runtime entrypoints/CLI explicitly open it; LLM receives ownership and closes
    its supplied database on shutdown. Standalone callers close their own instance.
    Registries borrow it and use transaction() for standalone writes or to join
    an enclosing transaction() such as a legacy import. This does not join a
    Store transaction or an arbitrary externally issued BEGIN. Preparation is
    explicit; construction only accepts an already prepared owned connection.
    """

    def __init__(self, db: sqlite3.Connection):
        """Take ownership of a prepared connection without SQL or file creation."""
        if not isinstance(db, sqlite3.Connection):
            raise TypeError("Use open_existing() or create() with a path")
        self.db = db
        self._transaction_depth = 0

    @classmethod
    def open_existing(cls, path: str, *, readonly: bool = False):
        db = open_readonly(path) if readonly else open_existing(path)
        try:
            require_current(db, ('admin',))
            if not readonly:
                db.execute('PRAGMA journal_mode=WAL')
            return cls(db)
        except BaseException:
            db.close()
            raise

    @classmethod
    def create(cls, path: str):
        """Explicit initialization for new/isolated databases, never an upgrade."""
        if not path:
            raise ValueError('DATABASE_PATH가 필요합니다.')
        path = str(path)
        if path != ':memory:':
            path = str(Path(path).expanduser())
        if path != ':memory:' and Path(path).exists():
            return cls.open_existing(path)
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with Path(path).open('xb'):
                pass
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        try:
            prepare_connection(db, ('admin',))
            db.execute('PRAGMA journal_mode=WAL')
            return cls(db)
        except BaseException:
            db.close()
            raise

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
