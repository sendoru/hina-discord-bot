from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class AdminRepository:
    """Small read-only query layer over the bot SQLite database."""

    def __init__(self, path: str | Path):
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise FileNotFoundError(f"Dashboard database is not a file: {resolved}")
        self.path = resolved
        self._uri = f"{resolved.as_uri()}?mode=ro"

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._uri, uri=True, timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _limit(value: int, *, maximum: int = 1000) -> int:
        value = int(value)
        if not 1 <= value <= maximum:
            raise ValueError(f"limit must be between 1 and {maximum}")
        return value

    @staticmethod
    def _dicts(rows) -> list[dict[str, object]]:
        return [dict(row) for row in rows]

    def ping(self) -> None:
        with self._connection() as db:
            db.execute("SELECT 1").fetchone()

    def table_names(self) -> tuple[str, ...]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        return tuple(str(row["name"]) for row in rows)

    def _has_column(self, table: str, column: str) -> bool:
        if table not in {"turns"}:
            raise ValueError("unsupported dashboard schema lookup")
        with self._connection() as db:
            rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        return any(str(row["name"]) == column for row in rows)

    def recent_turns(self, *, limit: int = 100) -> list[dict[str, object]]:
        limit = self._limit(limit)
        turn_id = "turn_id" if self._has_column("turns", "turn_id") else "NULL AS turn_id"
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT id,scope,realm,user_id,message_id,content,reply,exportable,
                           {turn_id},memory_context,created_at
                    FROM turns ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return self._dicts(rows)

    @staticmethod
    def _like(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

    def _turn_filters(
        self,
        *,
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        query: str = "",
    ) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = []
        params: list[object] = []
        if scope:
            clauses.append("scope=?")
            params.append(scope)
        if realm:
            clauses.append("realm=?")
            params.append(realm)
        if user_id:
            clauses.append("user_id=?")
            params.append(user_id)
        if query:
            escaped = self._like(query)
            clauses.append(
                "(content LIKE ? ESCAPE '\\' OR reply LIKE ? ESCAPE '\\' "
                "OR message_id LIKE ? ESCAPE '\\')"
            )
            pattern = f"%{escaped}%"
            params.extend((pattern, pattern, pattern))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, tuple(params)

    def count_turns(
        self,
        *,
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        query: str = "",
    ) -> int:
        where, params = self._turn_filters(
            scope=scope, realm=realm, user_id=user_id, query=query
        )
        with self._connection() as db:
            row = db.execute(f"SELECT COUNT(*) AS count FROM turns{where}", params).fetchone()
        return int(row["count"]) if row is not None else 0

    def search_turns(
        self,
        *,
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        limit = self._limit(limit)
        offset = max(0, int(offset))
        turn_id = "turn_id" if self._has_column("turns", "turn_id") else "NULL AS turn_id"
        where, params = self._turn_filters(
            scope=scope, realm=realm, user_id=user_id, query=query
        )
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT id,scope,realm,user_id,message_id,content,reply,exportable,
                           {turn_id},memory_context,created_at
                    FROM turns{where} ORDER BY id DESC LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            ).fetchall()
        return self._dicts(rows)

    def turn_for_trace(self, turn_id: str) -> dict[str, object] | None:
        trace_id = turn_id.strip()
        if not trace_id or not self._has_column("turns", "turn_id"):
            return None
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM turns WHERE turn_id=? ORDER BY id DESC LIMIT 1",
                (trace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def recent_memory_items(self, *, limit: int = 100) -> list[dict[str, object]]:
        limit = self._limit(limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM memory_items ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return self._dicts(rows)

    def recent_reconciliation_proposals(
        self, *, limit: int = 100
    ) -> list[dict[str, object]]:
        limit = self._limit(limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM memory_reconciliation_proposals ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return self._dicts(rows)

    def personal_summaries(self, *, limit: int = 100) -> list[dict[str, object]]:
        limit = self._limit(limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM summaries ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return self._dicts(rows)

    def shared_summaries(self, *, limit: int = 100) -> list[dict[str, object]]:
        limit = self._limit(limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM shared_summaries ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return self._dicts(rows)
