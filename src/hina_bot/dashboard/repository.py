from __future__ import annotations

import json
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

    def _table_exists(self, table: str) -> bool:
        with self._connection() as db:
            row = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        return row is not None

    def _table_columns(self, table: str) -> set[str]:
        if not self._table_exists(table):
            return set()
        with self._connection() as db:
            rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"]) for row in rows}

    @staticmethod
    def _decode_json(value: object, default):
        if not isinstance(value, str) or not value:
            return default
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default

    def _memory_filters(
        self,
        *,
        user_id: str = "",
        origin_realm: str = "",
        origin_channel_id: str = "",
        kind: str = "",
        disclosure: str = "",
        status: str = "",
        relationship: str = "",
        query: str = "",
        confidence_min: float | None = None,
        confidence_max: float | None = None,
        created_after: str = "",
        created_before: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> tuple[str, tuple[object, ...]]:
        columns = self._table_columns("memory_items")
        clauses: list[str] = []
        params: list[object] = []
        exact = {
            "user_id": user_id,
            "origin_realm": origin_realm,
            "origin_channel_id": origin_channel_id,
            "kind": kind,
            "disclosure": disclosure,
        }
        for column, value in exact.items():
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if status:
            if "status" in columns:
                clauses.append("status=?")
                params.append(status)
            else:
                clauses.append("1=0")
        if relationship == "yes":
            clauses.append("relationship_evidence NOT IN ('{}','')")
        elif relationship == "no":
            clauses.append("relationship_evidence IN ('{}','')")
        if confidence_min is not None:
            clauses.append("confidence>=?")
            params.append(float(confidence_min))
        if confidence_max is not None:
            clauses.append("confidence<=?")
            params.append(float(confidence_max))
        for column, operator, value in (
            ("created_at", ">=", created_after),
            ("created_at", "<=", created_before),
            ("updated_at", ">=", updated_after),
            ("updated_at", "<=", updated_before),
        ):
            if value:
                clauses.append(f"{column}{operator}?")
                params.append(value)
        if query:
            escaped = self._like(query)
            pattern = f"%{escaped}%"
            clauses.append(
                "(content LIKE ? ESCAPE '\\' OR source_message_ids LIKE ? ESCAPE '\\')"
            )
            params.extend((pattern, pattern))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, tuple(params)

    def memory_schema(self) -> dict[str, object]:
        columns = self._table_columns("memory_items")
        return {
            "available": bool(columns),
            "columns": tuple(sorted(columns)),
            "has_lifecycle": "status" in columns,
            "has_superseded_by": "superseded_by" in columns,
        }

    def count_memory_items(self, **filters) -> int:
        if not self._table_exists("memory_items"):
            return 0
        where, params = self._memory_filters(**filters)
        with self._connection() as db:
            row = db.execute(
                f"SELECT COUNT(*) AS count FROM memory_items{where}", params
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def search_memory_items(
        self, *, limit: int = 50, offset: int = 0, **filters
    ) -> list[dict[str, object]]:
        if not self._table_exists("memory_items"):
            return []
        limit = self._limit(limit)
        offset = max(0, int(offset))
        where, params = self._memory_filters(**filters)
        with self._connection() as db:
            rows = db.execute(
                f"SELECT * FROM memory_items{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return self._dicts(rows)

    def memory_item(self, item_id: int) -> dict[str, object] | None:
        if not self._table_exists("memory_items"):
            return None
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM memory_items WHERE id=?", (int(item_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def neighboring_memory_items(
        self, item: dict[str, object], *, limit: int = 8
    ) -> list[dict[str, object]]:
        if not self._table_exists("memory_items"):
            return []
        limit = self._limit(limit, maximum=50)
        with self._connection() as db:
            rows = db.execute(
                """SELECT * FROM memory_items
                   WHERE id!=? AND user_id=? AND origin_realm=? AND origin_channel_id=?
                   ORDER BY ABS(id-?) ASC LIMIT ?""",
                (
                    int(item["id"]),
                    str(item["user_id"]),
                    str(item["origin_realm"]),
                    str(item["origin_channel_id"]),
                    int(item["id"]),
                    limit,
                ),
            ).fetchall()
        return self._dicts(rows)

    def turns_for_message_ids(self, message_ids: list[str]) -> list[dict[str, object]]:
        values = tuple(dict.fromkeys(str(value) for value in message_ids if str(value)))
        if not values or not self._table_exists("turns"):
            return []
        placeholders = ",".join("?" for _ in values)
        turn_id = "turn_id" if self._has_column("turns", "turn_id") else "NULL AS turn_id"
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT id,scope,realm,user_id,message_id,content,reply,exportable,
                           {turn_id},memory_context,created_at
                    FROM turns WHERE message_id IN ({placeholders}) ORDER BY id""",
                values,
            ).fetchall()
        return self._dicts(rows)

    def memory_counts_by_user(self) -> dict[str, int]:
        if not self._table_exists("memory_items"):
            return {}
        with self._connection() as db:
            rows = db.execute(
                "SELECT user_id,COUNT(*) AS count FROM memory_items GROUP BY user_id"
            ).fetchall()
        return {str(row["user_id"]): int(row["count"]) for row in rows}

    def personal_summary_status(self) -> list[dict[str, object]]:
        if not self._table_exists("summaries"):
            return []
        with self._connection() as db:
            rows = db.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM turns t
                           WHERE t.scope=s.scope AND t.id>s.through_id) AS pending_turns,
                          (SELECT MAX(id) FROM turns t WHERE t.scope=s.scope) AS latest_turn_id
                   FROM summaries s ORDER BY s.rowid DESC"""
            ).fetchall()
        return self._dicts(rows)

    def shared_summary_status(self) -> list[dict[str, object]]:
        if not self._table_exists("shared_summaries"):
            return []
        has_calls = self._table_exists("shared_calls")
        pending = (
            """(SELECT COUNT(*) FROM shared_calls c
                 WHERE c.scope=s.scope AND c.id>s.through_id)"""
            if has_calls
            else "0"
        )
        latest = (
            "(SELECT MAX(id) FROM shared_calls c WHERE c.scope=s.scope)"
            if has_calls
            else "NULL"
        )
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT s.*, {pending} AS pending_calls,
                           {latest} AS latest_call_id
                    FROM shared_summaries s ORDER BY s.rowid DESC"""
            ).fetchall()
        return self._dicts(rows)

    def extraction_cursor_status(self) -> list[dict[str, object]]:
        if not self._table_exists("turns"):
            return []
        has_cursors = self._table_exists("memory_extraction_cursors")
        has_summaries = self._table_exists("summaries")

        scope_parts = ["SELECT scope, realm, user_id FROM turns"]
        if has_summaries:
            scope_parts.append("SELECT scope, realm, user_id FROM summaries")
        if has_cursors:
            scope_parts.append(
                "SELECT scope, realm, user_id FROM memory_extraction_cursors"
            )
        scopes_sql = " UNION ".join(scope_parts)

        cursor_join = (
            "LEFT JOIN memory_extraction_cursors c ON c.scope=sc.scope"
            if has_cursors
            else ""
        )
        summary_join = (
            "LEFT JOIN summaries s ON s.scope=sc.scope" if has_summaries else ""
        )
        cursor_fields = (
            "c.through_id AS extraction_through_id, "
            "c.updated_at AS extraction_updated_at,"
            if has_cursors
            else "NULL AS extraction_through_id, NULL AS extraction_updated_at,"
        )
        summary_fields = (
            "s.through_id AS summary_through_id,"
            if has_summaries
            else "NULL AS summary_through_id,"
        )
        if has_cursors and has_summaries:
            effective = "COALESCE(c.through_id,s.through_id,0)"
        elif has_cursors:
            effective = "COALESCE(c.through_id,0)"
        elif has_summaries:
            effective = "COALESCE(s.through_id,0)"
        else:
            effective = "0"
        initialized = "CASE WHEN c.scope IS NULL THEN 0 ELSE 1 END" if has_cursors else "0"

        with self._connection() as db:
            rows = db.execute(
                f"""WITH scopes AS ({scopes_sql})
                    SELECT sc.scope,sc.realm,sc.user_id,
                           {cursor_fields}
                           {summary_fields}
                           {effective} AS effective_through_id,
                           {initialized} AS initialized,
                           (SELECT COUNT(*) FROM turns t
                            WHERE t.scope=sc.scope AND t.id>{effective}) AS pending_turns,
                           (SELECT MAX(id) FROM turns t WHERE t.scope=sc.scope) AS latest_turn_id
                    FROM scopes sc
                    {cursor_join}
                    {summary_join}
                    ORDER BY pending_turns DESC, sc.scope"""
            ).fetchall()
        return self._dicts(rows)

    def recent_memory_items(self, *, limit: int = 100) -> list[dict[str, object]]:
        limit = self._limit(limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM memory_items ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return self._dicts(rows)

    @staticmethod
    def _proposal_select() -> str:
        return """
            SELECT p.*,
                   n.content AS new_content,
                   n.kind AS new_kind,
                   n.disclosure AS new_disclosure,
                   n.confidence AS new_confidence,
                   n.source_message_ids AS new_source_message_ids,
                   n.relationship_evidence AS new_relationship_evidence,
                   n.created_at AS new_created_at,
                   n.updated_at AS new_updated_at,
                   t.content AS target_content,
                   t.kind AS target_kind,
                   t.disclosure AS target_disclosure,
                   t.confidence AS target_confidence,
                   t.source_message_ids AS target_source_message_ids,
                   t.relationship_evidence AS target_relationship_evidence,
                   t.created_at AS target_created_at,
                   t.updated_at AS target_updated_at,
                   CASE
                     WHEN json_array_length(n.source_message_ids)>0
                          AND NOT EXISTS (
                              SELECT value FROM json_each(n.source_message_ids)
                              EXCEPT
                              SELECT value FROM json_each(t.source_message_ids)
                          )
                          AND NOT EXISTS (
                              SELECT value FROM json_each(t.source_message_ids)
                              EXCEPT
                              SELECT value FROM json_each(n.source_message_ids)
                          )
                     THEN 1 ELSE 0
                   END AS retry_suspect
            FROM memory_reconciliation_proposals p
            JOIN memory_items n ON n.id=p.new_memory_item_id
            JOIN memory_items t ON t.id=p.target_memory_item_id
        """

    def _proposal_filters(
        self,
        *,
        user_id: str = "",
        origin_realm: str = "",
        origin_channel_id: str = "",
        relation: str = "",
        kind: str = "",
        retry: str = "",
        query: str = "",
        confidence_min: float | None = None,
        confidence_max: float | None = None,
        created_after: str = "",
        created_before: str = "",
    ) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = []
        params: list[object] = []
        exact = {
            "p.user_id": user_id,
            "p.origin_realm": origin_realm,
            "p.origin_channel_id": origin_channel_id,
            "p.relation": relation,
        }
        for column, value in exact.items():
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if kind:
            clauses.append("(n.kind=? OR t.kind=?)")
            params.extend((kind, kind))
        if confidence_min is not None:
            clauses.append("p.confidence>=?")
            params.append(float(confidence_min))
        if confidence_max is not None:
            clauses.append("p.confidence<=?")
            params.append(float(confidence_max))
        if created_after:
            clauses.append("p.created_at>=?")
            params.append(created_after)
        if created_before:
            clauses.append("p.created_at<=?")
            params.append(created_before)
        retry_expr = """
            json_array_length(n.source_message_ids)>0
            AND NOT EXISTS (
                SELECT value FROM json_each(n.source_message_ids)
                EXCEPT
                SELECT value FROM json_each(t.source_message_ids)
            )
            AND NOT EXISTS (
                SELECT value FROM json_each(t.source_message_ids)
                EXCEPT
                SELECT value FROM json_each(n.source_message_ids)
            )
        """
        if retry == "yes":
            clauses.append(f"({retry_expr})")
        elif retry == "no":
            clauses.append(f"NOT ({retry_expr})")
        if query:
            escaped = self._like(query)
            pattern = f"%{escaped}%"
            clauses.append(
                """(
                    n.content LIKE ? ESCAPE '\\'
                    OR t.content LIKE ? ESCAPE '\\'
                    OR p.source_message_ids LIKE ? ESCAPE '\\'
                    OR CAST(p.id AS TEXT) LIKE ? ESCAPE '\\'
                )"""
            )
            params.extend((pattern, pattern, pattern, pattern))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, tuple(params)

    def reconciliation_schema(self) -> dict[str, object]:
        proposal_columns = self._table_columns("memory_reconciliation_proposals")
        memory_columns = self._table_columns("memory_items")
        return {
            "available": bool(proposal_columns and memory_columns),
            "proposal_columns": tuple(sorted(proposal_columns)),
            "memory_columns": tuple(sorted(memory_columns)),
        }

    def count_reconciliation_proposals(self, **filters) -> int:
        if not self.reconciliation_schema()["available"]:
            return 0
        where, params = self._proposal_filters(**filters)
        with self._connection() as db:
            row = db.execute(
                f"""SELECT COUNT(*) AS count
                    FROM memory_reconciliation_proposals p
                    JOIN memory_items n ON n.id=p.new_memory_item_id
                    JOIN memory_items t ON t.id=p.target_memory_item_id
                    {where}""",
                params,
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def search_reconciliation_proposals(
        self, *, limit: int = 50, offset: int = 0, **filters
    ) -> list[dict[str, object]]:
        if not self.reconciliation_schema()["available"]:
            return []
        limit = self._limit(limit)
        offset = max(0, int(offset))
        where, params = self._proposal_filters(**filters)
        with self._connection() as db:
            rows = db.execute(
                f"""{self._proposal_select()}
                    {where}
                    ORDER BY p.id DESC LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            ).fetchall()
        return self._dicts(rows)

    def reconciliation_proposal(self, proposal_id: int) -> dict[str, object] | None:
        if not self.reconciliation_schema()["available"]:
            return None
        with self._connection() as db:
            row = db.execute(
                f"""{self._proposal_select()}
                    WHERE p.id=?""",
                (int(proposal_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def reconciliation_stats(self, **filters) -> dict[str, object]:
        if not self.reconciliation_schema()["available"]:
            return {
                "total": 0,
                "average_confidence": None,
                "retry_suspects": 0,
                "relationship_proposals": 0,
                "relations": {},
            }
        where, params = self._proposal_filters(**filters)
        base = """
            FROM memory_reconciliation_proposals p
            JOIN memory_items n ON n.id=p.new_memory_item_id
            JOIN memory_items t ON t.id=p.target_memory_item_id
        """
        retry_expr = (
            "n.source_message_ids=t.source_message_ids "
            "AND n.source_message_ids NOT IN ('[]','')"
        )
        with self._connection() as db:
            summary = db.execute(
                f"""SELECT COUNT(*) AS total,
                           AVG(p.confidence) AS average_confidence,
                           SUM(CASE WHEN {retry_expr} THEN 1 ELSE 0 END)
                               AS retry_suspects,
                           SUM(CASE WHEN n.kind='relationship' OR t.kind='relationship'
                                    THEN 1 ELSE 0 END) AS relationship_proposals
                    {base}
                    {where}""",
                params,
            ).fetchone()
            relations = db.execute(
                f"""SELECT p.relation,COUNT(*) AS count
                    {base}
                    {where}
                    GROUP BY p.relation ORDER BY p.relation""",
                params,
            ).fetchall()
        return {
            "total": int(summary["total"] or 0),
            "average_confidence": (
                float(summary["average_confidence"])
                if summary["average_confidence"] is not None
                else None
            ),
            "retry_suspects": int(summary["retry_suspects"] or 0),
            "relationship_proposals": int(summary["relationship_proposals"] or 0),
            "relations": {
                str(row["relation"]): int(row["count"])
                for row in relations
            },
        }

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
