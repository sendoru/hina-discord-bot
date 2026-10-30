from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from hina_bot.core.db_connections import open_readonly
from hina_bot.core.db_schema import require_current


class AdminRepository:
    """Small read-only query layer over the bot SQLite database."""

    def __init__(self, path: str | Path):
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise FileNotFoundError(f"Dashboard database is not a file: {resolved}")
        self.path = resolved
        with self._connection() as db:
            require_current(db)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(open_readonly(self.path)) as db:
            yield db

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

    def runtime_config_startup_rows(self) -> list[dict[str, object]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT key,value,captured_at FROM runtime_config_startup ORDER BY key"
            ).fetchall()
        return self._dicts(rows)

    def runtime_config_rows(self) -> list[dict[str, object]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT key,value,updated_at FROM runtime_config ORDER BY key"
            ).fetchall()
        return self._dicts(rows)

    def admin_command_row(self, command_id: int) -> dict[str, object] | None:
        with self._connection() as db:
            row = db.execute(
                """SELECT id,request_id,actor,action,target,status,result_json,
                          error_type,error_message,created_at,started_at,finished_at
                   FROM admin_commands WHERE id=?""",
                (int(command_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def admin_command_id_for_request(self, request_id: str) -> int | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT id FROM admin_commands WHERE request_id=?", (request_id,)
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def admin_command_rows(
        self,
        *,
        action_prefix: str = "",
        limit: int = 50,
    ) -> list[dict[str, object]]:
        limit = self._limit(limit, maximum=200)
        with self._connection() as db:
            if action_prefix:
                rows = db.execute(
                    """SELECT id,request_id,actor,action,target,status,result_json,
                              error_type,error_message,created_at,started_at,finished_at
                       FROM admin_commands
                       WHERE action LIKE ?
                       ORDER BY id DESC LIMIT ?""",
                    (action_prefix + "%", limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT id,request_id,actor,action,target,status,result_json,
                              error_type,error_message,created_at,started_at,finished_at
                       FROM admin_commands
                       ORDER BY id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return self._dicts(rows)

    def instruction_rows(self) -> list[dict[str, object]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT id,text,enabled,created_at,updated_at
                   FROM instructions ORDER BY rowid"""
            ).fetchall()
        return self._dicts(rows)

    def runtime_knowledge_rows(self) -> list[dict[str, object]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT id,kind,content,keywords,subjects,awareness,timeline,
                          enabled,created_at,updated_at
                   FROM runtime_knowledge
                   ORDER BY kind,rowid"""
            ).fetchall()
        return self._dicts(rows)

    def observability_epochs(self) -> list[dict[str, object]]:
        if not self._table_exists("observability_epochs"):
            return []
        with self._connection() as db:
            rows = db.execute(
                "SELECT id,reset_at FROM observability_epochs ORDER BY id"
            ).fetchall()
        return self._dicts(rows)

    def scope_mode_overrides(self, table: str) -> list[dict[str, object]]:
        if table not in {"memory_modes", "chat_log_modes"}:
            raise ValueError("unsupported dashboard mode table")
        with self._connection() as db:
            rows = db.execute(
                f"SELECT scope,mode FROM {table} ORDER BY scope"
            ).fetchall()
        return self._dicts(rows)

    @staticmethod
    def _channel_id_from_scope(scope: object, realm: str) -> str:
        text = str(scope or "")
        prefix = f"{realm}:channel:"
        if not text.startswith(prefix):
            return ""
        channel = text[len(prefix):].split(":", 1)[0]
        if not channel.isdigit() or int(channel) <= 0:
            return ""
        return channel

    def dm_channel_candidates(self, user_id: str) -> list[str]:
        """Return DM channel IDs observed for one user across retained state."""

        user_id = str(user_id).strip()
        if not user_id.isdigit() or int(user_id) <= 0:
            return []
        realm = f"dm:{user_id}"
        channels: set[str] = set()

        for table in (
            "turns",
            "failed_turns",
            "shared_calls",
            "shared_summaries",
            "memory_extraction_cursors",
        ):
            with self._connection() as db:
                rows = db.execute(
                    f"SELECT scope FROM {table} WHERE realm=? AND user_id=?",
                    (realm, user_id),
                ).fetchall()
            for row in rows:
                channel = self._channel_id_from_scope(row["scope"], realm)
                if channel:
                    channels.add(channel)

        with self._connection() as db:
            rows = db.execute(
                """SELECT origin_channel_id FROM memory_items
                   WHERE origin_realm=? AND user_id=?""",
                (realm, user_id),
            ).fetchall()
        for row in rows:
            channel = str(row["origin_channel_id"] or "")
            if channel.isdigit() and int(channel) > 0:
                channels.add(channel)

        with self._connection() as db:
            rows = db.execute(
                """SELECT origin_channel_id FROM memory_reconciliation_proposals
                   WHERE origin_realm=? AND user_id=?""",
                (realm, user_id),
            ).fetchall()
        for row in rows:
            channel = str(row["origin_channel_id"] or "")
            if channel.isdigit() and int(channel) > 0:
                channels.add(channel)

        for table in ("memory_modes", "chat_log_modes"):
            with self._connection() as db:
                rows = db.execute(
                    f"SELECT scope FROM {table} WHERE scope LIKE ?",
                    (f"{realm}:channel:%",),
                ).fetchall()
            for row in rows:
                channel = self._channel_id_from_scope(row["scope"], realm)
                if channel:
                    channels.add(channel)

        prefix = f"config:chatlog_capture:{realm}:channel:"
        with self._connection() as db:
            rows = db.execute(
                "SELECT scope FROM notes WHERE scope LIKE ?",
                (f"{prefix}%",),
            ).fetchall()
        for row in rows:
            channel = str(row["scope"] or "")[len(prefix):].split(":", 1)[0]
            if channel.isdigit() and int(channel) > 0:
                channels.add(channel)

        return sorted(channels, key=int)

    def note_rows(self) -> list[dict[str, object]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT scope,text FROM notes ORDER BY scope"
            ).fetchall()
        return self._dicts(rows)

    def latest_user_names(self) -> dict[tuple[str, str], str]:
        names: dict[tuple[str, str], str] = {}
        with self._connection() as db:
            rows = db.execute(
                """SELECT realm,user_id,name FROM turns
                   WHERE name<>'' ORDER BY id DESC"""
            ).fetchall()
        for row in rows:
            key = (str(row["realm"]), str(row["user_id"]))
            names.setdefault(key, str(row["name"])[:100])
        with self._connection() as db:
            rows = db.execute(
                """SELECT origin_realm AS realm,user_id,user_name
                   FROM memory_items WHERE user_name<>''
                   ORDER BY id DESC"""
            ).fetchall()
        for row in rows:
            key = (str(row["realm"]), str(row["user_id"]))
            names.setdefault(key, str(row["user_name"])[:100])

        with self._connection() as db:
            rows = db.execute(
                """SELECT realm,user_id,name FROM shared_summaries
                   WHERE name<>'' ORDER BY rowid DESC"""
            ).fetchall()
        for row in rows:
            key = (str(row["realm"]), str(row["user_id"]))
            names.setdefault(key, str(row["name"])[:100])
        return names

    def discord_scope_metadata(self) -> dict[str, dict[str, object]]:
        """Return latest human-readable Discord guild/channel metadata when available."""
        with self._connection() as db:
            rows = db.execute(
                "SELECT guild_id,name,updated_at FROM guild_metadata ORDER BY guild_id"
            ).fetchall()
        guilds = {
            str(row["guild_id"]): {
                "name": str(row["name"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        }
        with self._connection() as db:
            rows = db.execute(
                """SELECT channel_id,guild_id,name,updated_at
                   FROM channel_metadata ORDER BY channel_id"""
            ).fetchall()
        channels = {
            str(row["channel_id"]): {
                "guild_id": str(row["guild_id"] or ""),
                "name": str(row["name"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        }
        return {"guilds": guilds, "channels": channels}

    @staticmethod
    def _has_debug_archive(db: sqlite3.Connection) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='conversation_debug_archive'"
        ).fetchone() is not None

    @classmethod
    def _turn_sources(cls, db: sqlite3.Connection, fields: str) -> str:
        source = f"SELECT {fields} FROM turns"
        if cls._has_debug_archive(db):
            source += (
                f" UNION ALL SELECT {fields} FROM conversation_debug_archive"
                " WHERE record_type='turn'"
            )
        return source

    def recent_turns(self, *, limit: int = 100) -> list[dict[str, object]]:
        limit = self._limit(limit)
        fields = (
            "id,scope,realm,user_id,name,message_id,content,reply,exportable,"
            "turn_id,memory_context,created_at"
        )
        with self._connection() as db:
            source = self._turn_sources(db, fields)
            rows = db.execute(
                f"SELECT * FROM ({source}) ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return self._dicts(rows)

    @staticmethod
    def _like(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

    def count_turns(self) -> int:
        """Successful-turn total including saved pre-reset debugging history."""
        with self._connection() as db:
            source = self._turn_sources(db, "id")
            return int(db.execute(
                f"SELECT COUNT(*) FROM ({source})"
            ).fetchone()[0])

    @classmethod
    def _conversation_union(cls, db: sqlite3.Connection) -> str:
        sql = """SELECT 'turn' AS record_type,id,scope,realm,user_id,name,message_id,
                         content,reply,exportable,turn_id,memory_context,created_at,
                         'completed' AS status,'' AS stage,1 AS reply_delivered,
                         '' AS error_type,'' AS error_fingerprint
                  FROM turns
                  UNION ALL
                  SELECT 'failed' AS record_type,id,scope,realm,user_id,name,message_id,
                         content,reply,0 AS exportable,turn_id,'' AS memory_context,created_at,
                         status,stage,reply_delivered,error_type,error_fingerprint
                  FROM failed_turns"""
        if cls._has_debug_archive(db):
            sql += """
                  UNION ALL
                  SELECT record_type,id,scope,realm,user_id,name,message_id,
                         content,reply,exportable,turn_id,memory_context,created_at,
                         status,stage,reply_delivered,error_type,error_fingerprint
                  FROM conversation_debug_archive"""
        return sql

    def _conversation_filters(
        self,
        *,
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        query: str = "",
        created_after: str = "",
        created_before: str = "",
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
            escaped_user = self._like(user_id)
            clauses.append(
                "(user_id=? OR name LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend((user_id, f"%{escaped_user}%"))
        if query:
            escaped = self._like(query)
            pattern = f"%{escaped}%"
            clauses.append(
                "(content LIKE ? ESCAPE '\\' OR reply LIKE ? ESCAPE '\\' "
                "OR message_id LIKE ? ESCAPE '\\' OR status LIKE ? ESCAPE '\\' "
                "OR stage LIKE ? ESCAPE '\\' OR error_type LIKE ? ESCAPE '\\' "
                "OR error_fingerprint LIKE ? ESCAPE '\\')"
            )
            params.extend((pattern,) * 7)
        if created_after:
            clauses.append("created_at>=?")
            params.append(created_after)
        if created_before:
            clauses.append("created_at<=?")
            params.append(created_before)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, tuple(params)

    def count_conversations(
        self,
        *,
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        query: str = "",
        created_after: str = "",
        created_before: str = "",
    ) -> int:
        where, params = self._conversation_filters(
            scope=scope,
            realm=realm,
            user_id=user_id,
            query=query,
            created_after=created_after,
            created_before=created_before,
        )
        with self._connection() as db:
            union = self._conversation_union(db)
            row = db.execute(
                f"SELECT COUNT(*) AS count FROM ({union}) conversations{where}",
                params,
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def search_conversations(
        self,
        *,
        scope: str = "",
        realm: str = "",
        user_id: str = "",
        query: str = "",
        created_after: str = "",
        created_before: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        limit = self._limit(limit)
        offset = max(0, int(offset))
        where, params = self._conversation_filters(
            scope=scope,
            realm=realm,
            user_id=user_id,
            query=query,
            created_after=created_after,
            created_before=created_before,
        )
        with self._connection() as db:
            union = self._conversation_union(db)
            rows = db.execute(
                f"""SELECT * FROM ({union}) conversations{where}
                    ORDER BY created_at DESC,record_type,id DESC
                    LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            ).fetchall()
        return self._dicts(rows)

    def turn_by_id(self, row_id: int) -> dict[str, object] | None:
        fields = (
            "id,scope,realm,user_id,name,message_id,content,reply,exportable,"
            "turn_id,memory_context,created_at"
        )
        with self._connection() as db:
            source = self._turn_sources(db, fields)
            row = db.execute(
                f"SELECT * FROM ({source}) WHERE id=?", (int(row_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def turn_context(
        self,
        row_id: int,
        *,
        before: int = 5,
        after: int = 5,
    ) -> list[dict[str, object]]:
        target = self.turn_by_id(row_id)
        if target is None:
            return []
        before = min(20, max(0, int(before)))
        after = min(20, max(0, int(after)))
        fields = (
            "id,scope,realm,user_id,name,message_id,content,reply,exportable,"
            "turn_id,memory_context,created_at"
        )
        with self._connection() as db:
            source = self._turn_sources(db, fields)
            previous = db.execute(
                f"""SELECT * FROM ({source})
                    WHERE scope=? AND id<?
                    ORDER BY id DESC LIMIT ?""",
                (str(target["scope"]), int(row_id), before),
            ).fetchall()
            following = db.execute(
                f"""SELECT * FROM ({source})
                    WHERE scope=? AND id>?
                    ORDER BY id ASC LIMIT ?""",
                (str(target["scope"]), int(row_id), after),
            ).fetchall()
        return [
            *reversed(self._dicts(previous)),
            target,
            *self._dicts(following),
        ]

    def turn_for_trace(self, turn_id: str) -> dict[str, object] | None:
        trace_id = turn_id.strip()
        if not trace_id:
            return None
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM turns WHERE turn_id=? ORDER BY id DESC LIMIT 1",
                (trace_id,),
            ).fetchone()
            if row is not None:
                return {**dict(row), "record_type": "turn"}
            row = db.execute(
                "SELECT * FROM failed_turns WHERE turn_id=? ORDER BY id DESC LIMIT 1",
                (trace_id,),
            ).fetchone()
            if row is None and self._has_debug_archive(db):
                row = db.execute(
                    "SELECT * FROM conversation_debug_archive "
                    "WHERE turn_id=? ORDER BY id DESC LIMIT 1",
                    (trace_id,),
                ).fetchone()
                if row is not None:
                    return dict(row)
        if row is None:
            return None
        return {
            **dict(row), "record_type": "failed", "exportable": 0,
            "memory_context": "", "context_provenance": "",
        }

    def _table_exists(self, table: str) -> bool:
        with self._connection() as db:
            row = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        return row is not None

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
        origin_realm_prefix: str = "",
        origin_channel_id: str = "",
        kind: str = "",
        disclosure: str = "",
        status: str = "",
        relationship: str = "",
        annotation: str = "",
        query: str = "",
        confidence_min: float | None = None,
        confidence_max: float | None = None,
        created_after: str = "",
        created_before: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = []
        params: list[object] = []
        if user_id:
            escaped_user = self._like(user_id)
            clauses.append(
                "(user_id=? OR user_name LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend((user_id, f"%{escaped_user}%"))
        exact = {
            "origin_realm": origin_realm,
            "origin_channel_id": origin_channel_id,
            "kind": kind,
            "disclosure": disclosure,
        }
        for column, value in exact.items():
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if origin_realm_prefix and not origin_realm:
            escaped_realm_prefix = self._like(origin_realm_prefix)
            clauses.append("origin_realm LIKE ? ESCAPE '\\'")
            params.append(f"{escaped_realm_prefix}%")
        if status:
            clauses.append("status=?")
            params.append(status)
        if relationship == "yes":
            clauses.append("relationship_evidence NOT IN ('{}','')")
        elif relationship == "no":
            clauses.append("relationship_evidence IN ('{}','')")
        # Derived annotation review queues (#451): no queue table, only these views.
        annotated = (
            "EXISTS (SELECT 1 FROM memory_item_annotations a "
            "WHERE a.memory_item_id=memory_items.id{})"
        )
        if annotation == "missing":
            clauses.append(
                f"status='active' AND kind!='relationship' AND NOT {annotated.format('')}"
            )
        elif annotation == "stale":
            clauses.append("status='active' AND " + annotated.format(
                " AND a.item_revision<memory_items.revision"
            ))
        elif annotation == "flagged":
            clauses.append(annotated.format(" AND a.lint_flags NOT IN ('[]','')"))
        elif annotation in {"admin", "backfill"}:
            clauses.append(annotated.format(" AND a.annotator=?"))
            params.append(annotation)
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

    def count_memory_items(self, **filters) -> int:
        where, params = self._memory_filters(**filters)
        with self._connection() as db:
            row = db.execute(
                f"SELECT COUNT(*) AS count FROM memory_items{where}", params
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def search_memory_items(
        self, *, limit: int = 50, offset: int = 0, **filters
    ) -> list[dict[str, object]]:
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
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM memory_items WHERE id=?", (int(item_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def memory_item_annotation(self, item_id: int) -> dict[str, object] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM memory_item_annotations WHERE memory_item_id=?", (int(item_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def memory_item_edit_history(
        self, item_id: int, *, limit: int = 50
    ) -> list[dict[str, object]]:
        limit = self._limit(limit, maximum=200)
        with self._connection() as db:
            rows = db.execute(
                """SELECT id,memory_item_id,revision,content,kind,disclosure,confidence,
                          relationship_evidence,edited_at,admin_command_id
                   FROM memory_item_edit_history
                   WHERE memory_item_id=?
                   ORDER BY revision DESC,id DESC LIMIT ?""",
                (int(item_id), limit),
            ).fetchall()
        return self._dicts(rows)

    def neighboring_memory_items(
        self, item: dict[str, object], *, limit: int = 8
    ) -> list[dict[str, object]]:
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
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        fields = (
            "id,scope,realm,user_id,name,message_id,content,reply,exportable,"
            "turn_id,memory_context,created_at"
        )
        with self._connection() as db:
            source = self._turn_sources(db, fields)
            rows = db.execute(
                f"SELECT * FROM ({source}) WHERE message_id IN ({placeholders}) "
                "ORDER BY id",
                values,
            ).fetchall()
        return self._dicts(rows)

    def relationship_profile_users(
        self,
        *,
        query: str = "",
    ) -> list[dict[str, object]]:
        clauses = ["kind='relationship'", "status='active'"]
        params: list[object] = []
        if query:
            escaped = self._like(query)
            pattern = f"%{escaped}%"
            clauses.append(
                "(user_id LIKE ? ESCAPE '\\' OR user_name LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend((pattern, pattern))
        where = " AND ".join(clauses)
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT user_id,MAX(NULLIF(user_name,'')) AS user_name,
                           COUNT(*) AS observation_count,MAX(updated_at) AS latest_updated_at
                    FROM memory_items
                    WHERE {where}
                    GROUP BY user_id
                    ORDER BY latest_updated_at DESC, user_id""",
                tuple(params),
            ).fetchall()
        return self._dicts(rows)

    def relationship_profile_items(self, user_id: str) -> list[dict[str, object]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT * FROM memory_items
                   WHERE user_id=? AND kind='relationship' AND status='active'
                   ORDER BY id""",
                (str(user_id),),
            ).fetchall()
        return self._dicts(rows)

    def shared_summary_status(self) -> list[dict[str, object]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM shared_calls c
                           WHERE c.scope=s.scope AND c.id>s.through_id) AS pending_calls,
                          (SELECT MAX(id) FROM shared_calls c
                           WHERE c.scope=s.scope) AS latest_call_id
                   FROM shared_summaries s ORDER BY s.rowid DESC"""
            ).fetchall()
        return self._dicts(rows)

    def extraction_cursor_status(self) -> list[dict[str, object]]:
        with self._connection() as db:
            rows = db.execute(
                """WITH scopes AS (
                       SELECT scope,realm,user_id FROM turns
                       UNION
                       SELECT scope,realm,user_id FROM memory_extraction_cursors
                   )
                   SELECT sc.scope,sc.realm,sc.user_id,
                          c.through_id AS extraction_through_id,
                          c.updated_at AS extraction_updated_at,
                          COALESCE(c.through_id,0) AS effective_through_id,
                          CASE WHEN c.scope IS NULL THEN 0 ELSE 1 END AS initialized,
                          (SELECT COUNT(*) FROM turns t
                           WHERE t.scope=sc.scope AND t.id>COALESCE(c.through_id,0))
                              AS pending_turns,
                          (SELECT MAX(id) FROM turns t WHERE t.scope=sc.scope) AS latest_turn_id
                   FROM scopes sc
                   LEFT JOIN memory_extraction_cursors c ON c.scope=sc.scope
                   ORDER BY pending_turns DESC,sc.scope"""
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
        origin_realm_prefix: str = "",
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
        if origin_realm_prefix and not origin_realm:
            escaped_realm_prefix = self._like(origin_realm_prefix)
            clauses.append("p.origin_realm LIKE ? ESCAPE '\\'")
            params.append(f"{escaped_realm_prefix}%")
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

    def count_reconciliation_proposals(self, **filters) -> int:
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
        with self._connection() as db:
            row = db.execute(
                f"""{self._proposal_select()}
                    WHERE p.id=?""",
                (int(proposal_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def reconciliation_stats(self, **filters) -> dict[str, object]:
        where, params = self._proposal_filters(**filters)
        base = """
            FROM memory_reconciliation_proposals p
            JOIN memory_items n ON n.id=p.new_memory_item_id
            JOIN memory_items t ON t.id=p.target_memory_item_id
        """
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
