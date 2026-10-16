"""Conversation SQL using the runtime Store connection and existing retention policy."""

import json
import sqlite3

from .memory_context import CURRENT_CONTEXT_PROVENANCE, CURRENT_MEMORY_CONTEXT
from .observability import current_turn_id
from .routing import Scope


class ConversationRepository:
    """No connection ownership or schema initialization; commits retain legacy boundaries."""

    def __init__(self, db: sqlite3.Connection, history_turns: int = 12):
        self.db = db
        self.history_turns = history_turns

    def user_name(self, scope: Scope) -> str:
        """Return the latest observed human-readable name for this scoped user."""
        for sql in (
            "SELECT name FROM turns WHERE scope=? AND name<>'' ORDER BY id DESC LIMIT 1",
            "SELECT name FROM shared_calls WHERE scope=? AND name<>'' ORDER BY id DESC LIMIT 1",
            "SELECT name FROM shared_summaries WHERE scope=? AND name<>'' LIMIT 1",
            "SELECT name FROM conversation_identity_names WHERE scope=? LIMIT 1",
        ):
            row = self.db.execute(sql, (scope.conversation,)).fetchone()
            if row is not None and str(row[0] or "").strip():
                return str(row[0])[:100]
        return ""

    def latest_conversation_reset_cutoff(self) -> int:
        """Bound automatic Discord history backfill after an offline reset."""
        if self.db.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table'
               AND name='conversation_reset_epochs'"""
        ).fetchone() is None:
            return 0
        row = self.db.execute(
            "SELECT cutoff_message_id FROM conversation_reset_epochs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def history(self, scope: Scope):
        return list(reversed(self.db.execute(
            "SELECT * FROM turns WHERE scope=? ORDER BY id DESC LIMIT ?",
            (scope.conversation, self.history_turns)).fetchall()))

    def seen(self, message_id: int):
        return self.db.execute("SELECT 1 FROM turns WHERE message_id=?",
                               (str(message_id),)).fetchone() is not None

    def add(
        self,
        scope: Scope,
        message_id: int,
        content: str,
        reply: str,
        *,
        name: str = "",
        memory_context=None,
        context_provenance=None,
    ):
        # Legacy code chained this bit through the personal summary to prevent a mixed
        # summary from becoming public. Structured extraction tracks provenance per turn,
        # so the retained column can now represent capture visibility directly.
        exportable = scope.public_at_capture
        if memory_context is None:
            memory_context = CURRENT_MEMORY_CONTEXT.get()
            CURRENT_MEMORY_CONTEXT.set(())
        encoded_context = (
            json.dumps(list(memory_context), ensure_ascii=False, separators=(",", ":"))
            if memory_context
            else ""
        )
        if context_provenance is None:
            context_provenance = CURRENT_CONTEXT_PROVENANCE.get()
            CURRENT_CONTEXT_PROVENANCE.set(None)
        encoded_provenance = (
            json.dumps(context_provenance, ensure_ascii=False, separators=(",", ":"))
            if context_provenance
            else ""
        )
        with self.db:
            self.db.execute(
                "INSERT INTO turns(scope,realm,user_id,name,message_id,content,reply,exportable,"
                "turn_id,memory_context,context_provenance) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scope.conversation,
                    scope.realm,
                    str(scope.user_id),
                    str(name or "")[:100],
                    str(message_id),
                    content,
                    reply,
                    int(exportable),
                    current_turn_id(),
                    encoded_context,
                    encoded_provenance,
                ),
            )
            # Bound raw retention independently of persistent-memory extraction.
            self.db.execute("DELETE FROM turns WHERE scope=? AND id NOT IN "
                            "(SELECT id FROM turns WHERE scope=? ORDER BY id DESC LIMIT ?)",
                            (scope.conversation, scope.conversation, self.history_turns))

    def add_failed_turn(
        self,
        scope: Scope,
        message_id: int,
        content: str,
        *,
        name: str = "",
        reply: str = "",
        status: str,
        stage: str,
        reply_delivered: bool,
        error_type: str = "",
        error_fingerprint: str = "",
    ):
        """Persist a failed exchange for observability without feeding memory pipelines."""
        with self.db:
            self.db.execute(
                """INSERT INTO failed_turns(
                       scope,realm,user_id,name,message_id,content,reply,turn_id,
                       status,stage,reply_delivered,error_type,error_fingerprint
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    scope.conversation,
                    scope.realm,
                    str(scope.user_id),
                    str(name or "")[:100],
                    str(message_id),
                    content,
                    reply,
                    current_turn_id(),
                    str(status),
                    str(stage),
                    int(bool(reply_delivered)),
                    str(error_type or ""),
                    str(error_fingerprint or ""),
                ),
            )
            self.db.execute(
                """DELETE FROM failed_turns
                   WHERE scope=? AND id NOT IN (
                       SELECT id FROM failed_turns
                       WHERE scope=? ORDER BY id DESC LIMIT ?
                   )""",
                (scope.conversation, scope.conversation, self.history_turns),
            )

    def add_shared_call(self, scope, message_id, name, content):
        if scope.guild_id is None or not scope.public_at_capture:
            return
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO shared_calls "
                            "(scope,realm,user_id,message_id,name,content) VALUES (?,?,?,?,?,?)",
                            (scope.conversation, scope.realm, str(scope.user_id), str(message_id),
                             name[:100], content))
            self.db.execute("DELETE FROM shared_calls WHERE scope=? AND id NOT IN "
                            "(SELECT id FROM shared_calls WHERE scope=? ORDER BY id DESC LIMIT ?)",
                            (scope.conversation, scope.conversation, self.history_turns))

    def shared_summary(self, scope):
        row = self.db.execute("SELECT text,through_id FROM shared_summaries WHERE scope=?",
                              (scope.conversation,)).fetchone()
        return (row[0], row[1]) if row else ("", 0)

    def pending_shared(self, scope):
        return self.db.execute("SELECT * FROM shared_calls WHERE scope=? AND id>? ORDER BY id",
                               (scope.conversation, self.shared_summary(scope)[1])).fetchall()

    def save_shared_summary(self, scope, name, text, through):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO shared_summaries VALUES (?,?,?,?,?,?)",
                            (scope.conversation, scope.realm, str(scope.user_id), name,
                             text[:1500], through))

    def public_candidates(self, user_id: int, guild_id: int | None = None):
        # Server queries include all speakers in this guild. DM queries include only its owner.
        condition, value = (("realm=?", f"guild:{guild_id}") if guild_id is not None
                            else ("user_id=?", str(user_id)))
        rows = self.db.execute(
            "SELECT scope, user_id, MAX(id) AS recent FROM shared_calls WHERE " + condition +
            " GROUP BY scope,user_id ORDER BY recent DESC LIMIT 30", (value,)).fetchall()
        return [Scope(int(r["scope"].split(":")[1]), int(r["scope"].split(":")[3]),
                      int(r["user_id"])) for r in rows]

    def public_context(self, allowed_scopes):
        context = []
        for source in allowed_scopes[:4]:
            rows = self.db.execute("SELECT * FROM shared_calls WHERE scope=? ORDER BY id DESC LIMIT 2",
                                   (source.conversation,)).fetchall()
            context.append({"source": source.conversation, "user_id": str(source.user_id),
                            "name": rows[0]["name"] if rows else "",
                            "summary": self.shared_summary(source)[0],
                            "recent_user_messages": [r["content"] for r in reversed(rows)]})
        return context

