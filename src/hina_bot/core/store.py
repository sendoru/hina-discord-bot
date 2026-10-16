import hashlib
import json
import sqlite3
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .chatlog_modes import migrate_legacy_chatlog_settings
from .conversation_repository import ConversationRepository
from .memory_items import (
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    RelationshipEvidence,
)
from .memory_repository import MemoryRepository
from .routing import Scope
from .scope_overrides import resolve_scope_chain
from .settings_repository import SettingsRepository
from .store_schema import initialize_store_schema


class Store:
    """Compatibility facade and transaction coordinator for the runtime SQLite connection.

    Calls remain synchronous on the event-loop thread. Conversation and memory SQL
    live in repositories; batch commits and cross-table purges remain coordinated here.
    """

    def __init__(self, path: str, history_turns: int = 12):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.settings = SettingsRepository(self.db)
        self._conversations = ConversationRepository(self.db, history_turns)
        self._atomic_memory_batch_open = False
        self._memories = MemoryRepository(
            self.db,
            user_name=lambda scope: self.user_name(scope),
            write_context=lambda: self._memory_write(),
        )
        initialize_store_schema(self.db)
        # Apply legacy config migration when the store is opened, even for runtimes
        # that do not construct the Discord /chatlog command group.
        migrate_legacy_chatlog_settings(self)

    @property
    def history_turns(self) -> int:
        return self._conversations.history_turns

    @history_turns.setter
    def history_turns(self, value: int):
        self._conversations.history_turns = value

    def close(self):
        try:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            # A checkpoint failure must not prevent the connection from closing during shutdown.
            pass
        finally:
            self.db.close()

    def note(self, key: str) -> str:
        return self.settings.note(key)

    def set_note(self, key: str, text: str):
        return self.settings.set_note(key, text)

    def user_name(self, scope: Scope) -> str:
        return self._conversations.user_name(scope)

    def observe_guild_channel(
        self,
        guild_id: int,
        guild_name: str,
        channel_id: int,
        channel_name: str,
    ):
        """Persist the latest observed human-readable guild and channel names."""
        guild_name = str(guild_name)[:100]
        channel_name = str(channel_name)[:100]
        if not guild_name or not channel_name:
            return
        with self.db:
            self.db.execute(
                """INSERT INTO guild_metadata(guild_id,name,updated_at)
                   VALUES (?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(guild_id) DO UPDATE SET
                       name=excluded.name,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE guild_metadata.name<>excluded.name""",
                (str(guild_id), guild_name),
            )
            self.db.execute(
                """INSERT INTO channel_metadata(channel_id,guild_id,name,updated_at)
                   VALUES (?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(channel_id) DO UPDATE SET
                       guild_id=excluded.guild_id,
                       name=excluded.name,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE channel_metadata.guild_id<>excluded.guild_id
                      OR channel_metadata.name<>excluded.name""",
                (str(channel_id), str(guild_id), channel_name),
            )

    def latest_conversation_reset_cutoff(self) -> int:
        return self._conversations.latest_conversation_reset_cutoff()

    def history(self, scope: Scope):
        return self._conversations.history(scope)

    @staticmethod
    def _memory_batch_source_hash(rows) -> str:
        """Fingerprint the exact turns approved by the LLM before committing."""
        fields = (
            "id", "message_id", "content", "reply", "exportable",
            "memory_context", "context_provenance",
        )
        values = [[row[key] for key in fields] for row in rows]
        encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _memory_write(self):
        """Avoid inner sqlite3 context-manager commits during an atomic memory batch."""
        return nullcontext() if self._atomic_memory_batch_open else self.db

    @contextmanager
    def atomic_memory_batch(self, scope: Scope, pending, *, expected_cursor: int):
        """Commit items, reconciliation, cursor and journal in exactly one transaction.

        The external LLM request must finish before entry. All writes within this context
        use _memory_write; a failed/stale batch is fully rolled back without partial items.
        """
        if not pending:
            raise ValueError("Cannot commit an empty memory batch")
        if self._atomic_memory_batch_open or self.db.in_transaction:
            raise RuntimeError("A conflicting SQLite transaction is already active")
        first_id = int(pending[0]["id"])
        last_id = int(pending[-1]["id"])
        expected_cursor = int(expected_cursor)
        signature = self._memory_batch_source_hash(pending)
        self.db.execute("BEGIN IMMEDIATE")
        self._atomic_memory_batch_open = True
        try:
            row = self.db.execute(
                "SELECT through_id FROM memory_extraction_cursors WHERE scope=?",
                (scope.conversation,),
            ).fetchone()
            active_cursor = int(row["through_id"]) if row is not None else 0
            if active_cursor != expected_cursor:
                raise RuntimeError("Memory extraction cursor changed during LLM request")
            current = self.db.execute(
                """SELECT * FROM turns WHERE scope=? AND id>?
                   ORDER BY id LIMIT ?""",
                (scope.conversation, expected_cursor, len(pending)),
            ).fetchall()
            if self._memory_batch_source_hash(current) != signature:
                raise RuntimeError("Memory extraction source turns changed during LLM request")

            yield
            self.save_memory_extraction_cursor(scope, last_id)
            self.db.execute(
                """INSERT INTO memory_extraction_batches(
                    scope, start_id, through_id, source_hash
                ) VALUES (?,?,?,?)""",
                (scope.conversation, first_id, last_id, signature),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        finally:
            self._atomic_memory_batch_open = False

    def memory_extraction_cursor(self, scope: Scope) -> int:
        row = self.db.execute(
            "SELECT through_id FROM memory_extraction_cursors WHERE scope=?",
            (scope.conversation,),
        ).fetchone()
        if row is not None:
            return int(row["through_id"])
        with self._memory_write():
            self.db.execute(
                """INSERT OR IGNORE INTO memory_extraction_cursors(
                       scope,realm,user_id,through_id
                   ) VALUES (?,?,?,0)""",
                (scope.conversation, scope.realm, str(scope.user_id)),
            )
        return 0

    def pending_memory_extraction(self, scope: Scope, *, limit: int | None = None):
        through = self.memory_extraction_cursor(scope)
        sql = "SELECT * FROM turns WHERE scope=? AND id>? ORDER BY id"
        params: tuple[object, ...] = (scope.conversation, through)
        if limit is not None:
            sql += " LIMIT ?"
            params += (int(limit),)
        return self.db.execute(sql, params).fetchall()

    def stale_memory_extraction_scopes(
        self,
        *,
        min_pending: int = 2,
        stale_after_seconds: int = 8 * 60 * 60,
        limit: int = 32,
    ) -> list[Scope]:
        """Return oldest scopes whose structured-memory tail has gone stale."""

        minimum = max(1, int(min_pending))
        maximum = max(1, int(limit))
        modifier = f"-{max(0, int(stale_after_seconds))} seconds"
        rows = self.db.execute(
            """SELECT t.scope,t.realm,t.user_id,
                      COUNT(*) AS pending_turns,
                      MIN(t.created_at) AS oldest_created_at
               FROM turns t
               LEFT JOIN memory_extraction_cursors c ON c.scope=t.scope
               WHERE t.id > COALESCE(c.through_id,0)
               GROUP BY t.scope,t.realm,t.user_id
               HAVING COUNT(*) >= ?
                  AND MIN(t.created_at) <= datetime('now', ?)
               ORDER BY oldest_created_at,t.scope
               LIMIT ?""",
            (minimum, modifier, maximum),
        ).fetchall()

        scopes: list[Scope] = []
        for row in rows:
            parts = str(row["scope"]).split(":")
            if len(parts) != 6 or parts[2] != "channel" or parts[4] != "user":
                continue
            try:
                if parts[0] == "guild":
                    guild_id: int | None = int(parts[1])
                elif parts[0] == "dm":
                    guild_id = None
                else:
                    continue
                channel_id = int(parts[3])
                user_id = int(row["user_id"])
            except (TypeError, ValueError):
                continue
            scopes.append(Scope(guild_id, channel_id, user_id))
        return scopes

    def memory_extraction_evidence(
        self,
        scope: Scope,
        *,
        before_id: int,
        limit: int = 4,
    ):
        """Return recent turns before the current extraction batch in chronological order."""

        rows = self.db.execute(
            """SELECT * FROM turns
               WHERE scope=? AND id<?
               ORDER BY id DESC LIMIT ?""",
            (scope.conversation, int(before_id), max(0, int(limit))),
        ).fetchall()
        return list(reversed(rows))

    def save_memory_extraction_cursor(self, scope: Scope, through: int):
        with self._memory_write():
            self.db.execute(
                """INSERT INTO memory_extraction_cursors(scope,realm,user_id,through_id,updated_at)
                   VALUES (?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(scope) DO UPDATE SET
                       realm=excluded.realm,
                       user_id=excluded.user_id,
                       through_id=excluded.through_id,
                       updated_at=CURRENT_TIMESTAMP""",
                (scope.conversation, scope.realm, str(scope.user_id), int(through)),
            )

    def seen(self, message_id: int):
        return self._conversations.seen(message_id)

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
        return self._conversations.add(
            scope,
            message_id,
            content,
            reply,
            name=name,
            memory_context=memory_context,
            context_provenance=context_provenance,
        )

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
        return self._conversations.add_failed_turn(
            scope,
            message_id,
            content,
            name=name,
            reply=reply,
            status=status,
            stage=stage,
            reply_delivered=reply_delivered,
            error_type=error_type,
            error_fingerprint=error_fingerprint,
        )

    @staticmethod
    def _decode_memory_item(row) -> MemoryItem:
        return MemoryRepository._decode_memory_item(row)

    def add_memory_item(
        self,
        scope: Scope,
        content: str,
        *,
        kind: MemoryKind | str,
        disclosure: MemoryDisclosure | str,
        source_message_ids=(),
        confidence: float = 1.0,
        relationship_evidence: RelationshipEvidence | dict | None = None,
        user_name: str | None = None,
    ) -> int:
        return self._memories.add_memory_item(
            scope,
            content,
            kind=kind,
            disclosure=disclosure,
            source_message_ids=source_message_ids,
            confidence=confidence,
            relationship_evidence=relationship_evidence,
            user_name=user_name,
        )

    def memory_items(
        self,
        user_id: int | str,
        *,
        origin_realm: str | None = None,
        include_superseded: bool = False,
    ):
        return self._memories.memory_items(
            user_id,
            origin_realm=origin_realm,
            include_superseded=include_superseded,
        )

    def memory_item(self, item_id: int) -> MemoryItem | None:
        return self._memories.memory_item(item_id)

    def memory_item_edit_history(self, item_id: int):
        return self._memories.memory_item_edit_history(item_id)

    def revise_memory_item(
        self,
        item_id: int,
        *,
        expected_revision: int,
        content: str,
        kind: MemoryKind | str,
        disclosure: MemoryDisclosure | str,
        confidence: float,
        relationship_evidence: RelationshipEvidence | dict | None = None,
        admin_command_id: int | None = None,
    ) -> MemoryItem:
        return self._memories.revise_memory_item(
            item_id,
            expected_revision=expected_revision,
            content=content,
            kind=kind,
            disclosure=disclosure,
            confidence=confidence,
            relationship_evidence=relationship_evidence,
            admin_command_id=admin_command_id,
        )

    def retract_memory_item(
        self,
        item_id: int,
        *,
        expected_revision: int,
    ) -> MemoryItem:
        return self._memories.retract_memory_item(item_id, expected_revision=expected_revision)

    def reference_gated_memory_candidates(self, scope: Scope, *, limit: int = 24):
        return self._memories.reference_gated_memory_candidates(scope, limit=limit)

    def memory_reconciliation_candidates(self, scope: Scope, *, limit: int = 24):
        return self._memories.memory_reconciliation_candidates(scope, limit=limit)

    def add_memory_reconciliation_proposal(
        self,
        scope: Scope,
        *,
        new_memory_item_id: int,
        target_memory_item_id: int,
        relation: str,
        confidence: float,
        source_message_ids=(),
    ) -> int | None:
        return self._memories.add_memory_reconciliation_proposal(
            scope,
            new_memory_item_id=new_memory_item_id,
            target_memory_item_id=target_memory_item_id,
            relation=relation,
            confidence=confidence,
            source_message_ids=source_message_ids,
        )

    def memory_reconciliation_proposal_id(
        self,
        *,
        new_memory_item_id: int,
        target_memory_item_id: int,
        relation: str,
    ) -> int | None:
        return self._memories.memory_reconciliation_proposal_id(
            new_memory_item_id=new_memory_item_id,
            target_memory_item_id=target_memory_item_id,
            relation=relation,
        )

    def memory_reconciliation_proposals(self, user_id: int | str):
        return self._memories.memory_reconciliation_proposals(user_id)

    def apply_memory_reconciliation_proposal(
        self,
        proposal_id: int,
        *,
        min_confidence: float = 0.95,
    ) -> str:
        return self._memories.apply_memory_reconciliation_proposal(
            proposal_id, min_confidence=min_confidence,
        )

    def forget(self, scope: Scope):
        """Delete this user's automatically accumulated memory in the current realm."""
        with self.db:
            for table in (
                "turns",
                "shared_calls",
                "shared_summaries",
                "memory_extraction_cursors",
            ):
                self.db.execute(f"DELETE FROM {table} WHERE realm=? AND user_id=?",
                                (scope.realm, str(scope.user_id)))
            self.db.execute(
                """DELETE FROM memory_item_edit_history
                   WHERE memory_item_id IN (
                       SELECT id FROM memory_items WHERE origin_realm=? AND user_id=?
                   )""",
                (scope.realm, str(scope.user_id)),
            )
            self.db.execute(
                "DELETE FROM memory_items WHERE origin_realm=? AND user_id=?",
                (scope.realm, str(scope.user_id)),
            )
            self.db.execute(
                "DELETE FROM memory_reconciliation_proposals WHERE origin_realm=? AND user_id=?",
                (scope.realm, str(scope.user_id)),
            )

    @staticmethod
    def _rowcount(cursor) -> int:
        return max(0, int(cursor.rowcount or 0))

    def purge_channel_memory(self, scope: Scope) -> int:
        """Delete automatic persistent memory for every user in one channel."""
        prefix = scope.channel + ":user:%"
        deleted = 0
        with self.db:
            for table in (
                "turns",
                "shared_calls",
                "shared_summaries",
                "memory_extraction_cursors",
            ):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE scope LIKE ?", (prefix,))
                deleted += self._rowcount(cursor)
            self.db.execute(
                """DELETE FROM memory_item_edit_history
                   WHERE memory_item_id IN (
                       SELECT id FROM memory_items
                       WHERE origin_realm=? AND origin_channel_id=?
                   )""",
                (scope.realm, str(scope.channel_id)),
            )
            cursor = self.db.execute(
                "DELETE FROM memory_items WHERE origin_realm=? AND origin_channel_id=?",
                (scope.realm, str(scope.channel_id)),
            )
            deleted += self._rowcount(cursor)
            cursor = self.db.execute(
                """DELETE FROM memory_reconciliation_proposals
                   WHERE origin_realm=? AND origin_channel_id=?""",
                (scope.realm, str(scope.channel_id)),
            )
            deleted += self._rowcount(cursor)
        return deleted

    def purge_realm_memory(self, scope: Scope) -> int:
        """Delete automatic persistent memory in a guild/realm, preserving manual notes."""
        deleted = 0
        with self.db:
            for table in (
                "turns",
                "shared_calls",
                "shared_summaries",
                "memory_extraction_cursors",
            ):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE realm=?", (scope.realm,))
                deleted += self._rowcount(cursor)
            self.db.execute(
                """DELETE FROM memory_item_edit_history
                   WHERE memory_item_id IN (
                       SELECT id FROM memory_items WHERE origin_realm=?
                   )""",
                (scope.realm,),
            )
            cursor = self.db.execute(
                "DELETE FROM memory_items WHERE origin_realm=?",
                (scope.realm,),
            )
            deleted += self._rowcount(cursor)
            cursor = self.db.execute(
                "DELETE FROM memory_reconciliation_proposals WHERE origin_realm=?",
                (scope.realm,),
            )
            deleted += self._rowcount(cursor)
        return deleted

    def purge_all_memory(self) -> int:
        """Delete all automatic persistent memory while preserving notes and configuration."""
        deleted = 0
        with self.db:
            self.db.execute("DELETE FROM memory_item_edit_history")
            for table in (
                "turns",
                "shared_calls",
                "shared_summaries",
                "memory_items",
                "memory_extraction_cursors",
                "memory_reconciliation_proposals",
            ):
                cursor = self.db.execute(f"DELETE FROM {table}")
                deleted += self._rowcount(cursor)
        return deleted

    def add_shared_call(self, scope, message_id, name, content):
        return self._conversations.add_shared_call(scope, message_id, name, content)

    def shared_summary(self, scope):
        return self._conversations.shared_summary(scope)

    def pending_shared(self, scope):
        return self._conversations.pending_shared(scope)

    def save_shared_summary(self, scope, name, text, through):
        return self._conversations.save_shared_summary(scope, name, text, through)

    def public_candidates(self, user_id: int, guild_id: int | None = None):
        return self._conversations.public_candidates(user_id, guild_id)

    def public_context(self, allowed_scopes):
        return self._conversations.public_context(allowed_scopes)

    def emoji_rows(self):
        return self.settings.emoji_rows()

    def add_emoji(self, alias, emoji_id, description, source_guild_id=None):
        return self.settings.add_emoji(alias, emoji_id, description, source_guild_id)

    def edit_emoji(self, alias, description):
        return self.settings.edit_emoji(alias, description)

    def remove_emoji(self, alias):
        return self.settings.remove_emoji(alias)

    def memory_mode_override(self, key: str) -> str | None:
        return self.settings.memory_mode_override(key)

    def memory_mode_chain(self, scope: Scope) -> dict[str, str | None]:
        overrides = {
            "global": self.memory_mode_override("global"),
            scope.channel: self.memory_mode_override(scope.channel),
        }
        if scope.guild_id is not None:
            overrides[scope.realm] = self.memory_mode_override(scope.realm)
        return resolve_scope_chain(overrides, scope, default="normal")

    def memory_mode(self, scope: Scope) -> str:
        return str(self.memory_mode_chain(scope)["effective"])

    def set_memory_mode_override(self, key: str, mode: str | None):
        return self.settings.set_memory_mode_override(key, mode)

    def set_memory_mode(self, scope: Scope, mode: str):
        """Backward-compatible channel override setter."""
        self.set_memory_mode_override(scope.channel, mode)

    def memory_mode_overrides(self) -> dict[str, str]:
        return self.settings.memory_mode_overrides()

    def chat_log_mode_override(self, key: str) -> str | None:
        return self.settings.chat_log_mode_override(key)

    def chat_log_mode_chain(self, scope: Scope) -> dict[str, str | None]:
        overrides = {
            "global": self.chat_log_mode_override("global"),
            scope.channel: self.chat_log_mode_override(scope.channel),
        }
        if scope.guild_id is not None:
            overrides[scope.realm] = self.chat_log_mode_override(scope.realm)
        return resolve_scope_chain(overrides, scope, default="on")

    def chat_log_enabled(self, scope: Scope) -> bool:
        return self.chat_log_mode_chain(scope)["effective"] == "on"

    def set_chat_log_mode_override(self, key: str, mode: str | None):
        return self.settings.set_chat_log_mode_override(key, mode)

    def chat_log_mode_overrides(self) -> dict[str, str]:
        return self.settings.chat_log_mode_overrides()
