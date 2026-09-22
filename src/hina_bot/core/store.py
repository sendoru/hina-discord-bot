import json
import sqlite3
from pathlib import Path

from .memory_context import CURRENT_MEMORY_CONTEXT
from .memory_items import (
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    RelationshipEvidence,
)
from .observability import current_turn_id
from .routing import Scope


class Store:
    """Small single-process SQLite store. All calls run on the event-loop thread."""

    def __init__(self, path: str, history_turns: int = 12):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.history_turns = history_turns
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                message_id TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL, reply TEXT NOT NULL, exportable INTEGER NOT NULL,
                turn_id TEXT,
                memory_context TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS turns_scope ON turns(scope, id);
            CREATE INDEX IF NOT EXISTS turns_owner ON turns(realm, user_id);
            CREATE TABLE IF NOT EXISTS summaries (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL, through_id INTEGER NOT NULL, exportable INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                realm TEXT NOT NULL, user_id TEXT NOT NULL, message_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS shared_calls_scope ON shared_calls(scope, id);
            CREATE TABLE IF NOT EXISTS shared_summaries (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                name TEXT NOT NULL, text TEXT NOT NULL, through_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN (
                    'fact','event','preference','relationship','boundary','task'
                )),
                origin_realm TEXT NOT NULL,
                origin_channel_id TEXT NOT NULL,
                origin_public_at_capture INTEGER NOT NULL
                    CHECK(origin_public_at_capture IN (0,1)),
                disclosure TEXT NOT NULL CHECK(disclosure IN (
                    'local','implicit','reference_gated','global'
                )),
                source_message_ids TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence >= 0 AND confidence <= 1),
                relationship_evidence TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','superseded')),
                superseded_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(superseded_by IS NULL OR superseded_by != id)
            );
            CREATE INDEX IF NOT EXISTS memory_items_owner ON memory_items(user_id, id);
            CREATE INDEX IF NOT EXISTS memory_items_origin
                ON memory_items(origin_realm, origin_channel_id, user_id);
            CREATE TABLE IF NOT EXISTS memory_extraction_cursors (
                scope TEXT PRIMARY KEY,
                realm TEXT NOT NULL,
                user_id TEXT NOT NULL,
                through_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS memory_extraction_cursor_owner
                ON memory_extraction_cursors(realm, user_id);
            CREATE TABLE IF NOT EXISTS memory_reconciliation_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                origin_realm TEXT NOT NULL,
                origin_channel_id TEXT NOT NULL,
                new_memory_item_id INTEGER NOT NULL,
                target_memory_item_id INTEGER NOT NULL,
                relation TEXT NOT NULL CHECK(relation IN ('duplicate','corrects','conflicts')),
                confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                source_message_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(new_memory_item_id != target_memory_item_id),
                UNIQUE(new_memory_item_id, target_memory_item_id, relation)
            );
            CREATE INDEX IF NOT EXISTS memory_reconciliation_owner
                ON memory_reconciliation_proposals(user_id, origin_realm, origin_channel_id, id);
            CREATE TABLE IF NOT EXISTS emoji_registry (
                alias TEXT PRIMARY KEY, emoji_id TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
                source_guild_id TEXT
            );
            CREATE TABLE IF NOT EXISTS memory_modes (
                scope TEXT PRIMARY KEY,
                mode TEXT NOT NULL CHECK(mode IN ('normal','read_only','write_only','off'))
            );
            CREATE TABLE IF NOT EXISTS chat_log_modes (
                scope TEXT PRIMARY KEY,
                mode TEXT NOT NULL CHECK(mode IN ('on','off'))
            );
            CREATE TABLE IF NOT EXISTS notes (scope TEXT PRIMARY KEY, text TEXT NOT NULL);
        """)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(turns)")}
        if "memory_context" not in columns:
            with self.db:
                self.db.execute(
                    "ALTER TABLE turns ADD COLUMN memory_context TEXT NOT NULL DEFAULT ''"
                )
        if "turn_id" not in columns:
            with self.db:
                self.db.execute("ALTER TABLE turns ADD COLUMN turn_id TEXT")
        if "name" not in columns:
            with self.db:
                self.db.execute("ALTER TABLE turns ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        with self.db:
            self.db.execute("CREATE INDEX IF NOT EXISTS turns_turn_id ON turns(turn_id)")
        summary_columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(summaries)")
        }
        if "name" not in summary_columns:
            with self.db:
                self.db.execute(
                    "ALTER TABLE summaries ADD COLUMN name TEXT NOT NULL DEFAULT ''"
                )
        memory_columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(memory_items)")
        }
        if "relationship_evidence" not in memory_columns:
            with self.db:
                self.db.execute(
                    "ALTER TABLE memory_items "
                    "ADD COLUMN relationship_evidence TEXT NOT NULL DEFAULT '{}'"
                )
        if "user_name" not in memory_columns:
            with self.db:
                self.db.execute(
                    "ALTER TABLE memory_items ADD COLUMN user_name TEXT NOT NULL DEFAULT ''"
                )
        if "status" not in memory_columns:
            with self.db:
                self.db.execute(
                    "ALTER TABLE memory_items ADD COLUMN status TEXT NOT NULL DEFAULT 'active' "
                    "CHECK(status IN ('active','superseded'))"
                )
        if "superseded_by" not in memory_columns:
            with self.db:
                self.db.execute("ALTER TABLE memory_items ADD COLUMN superseded_by INTEGER")
        with self.db:
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS memory_items_owner_status "
                "ON memory_items(user_id,status,id)"
            )

    def close(self):
        try:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            # A checkpoint failure must not prevent the connection from closing during shutdown.
            pass
        finally:
            self.db.close()

    def note(self, key: str) -> str:
        row = self.db.execute("SELECT text FROM notes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else ""

    def set_note(self, key: str, text: str):
        with self.db:
            if text:
                self.db.execute("INSERT OR REPLACE INTO notes VALUES (?,?)", (key, text[:1500]))
            else:
                self.db.execute("DELETE FROM notes WHERE scope=?", (key,))

    def summary(self, scope: Scope):
        row = self.db.execute("SELECT text, through_id FROM summaries WHERE scope=?",
                              (scope.conversation,)).fetchone()
        return (row[0], row[1]) if row else ("", 0)

    def user_name(self, scope: Scope) -> str:
        """Return the latest observed human-readable name for this scoped user."""
        for sql in (
            "SELECT name FROM turns WHERE scope=? AND name<>'' ORDER BY id DESC LIMIT 1",
            "SELECT name FROM shared_calls WHERE scope=? AND name<>'' ORDER BY id DESC LIMIT 1",
            "SELECT name FROM shared_summaries WHERE scope=? AND name<>'' LIMIT 1",
        ):
            row = self.db.execute(sql, (scope.conversation,)).fetchone()
            if row is not None and str(row[0] or "").strip():
                return str(row[0])[:100]
        return ""

    def history(self, scope: Scope):
        return list(reversed(self.db.execute(
            "SELECT * FROM turns WHERE scope=? ORDER BY id DESC LIMIT ?",
            (scope.conversation, self.history_turns)).fetchall()))

    def pending(self, scope: Scope):
        _, through = self.summary(scope)
        return self.db.execute("SELECT * FROM turns WHERE scope=? AND id>? ORDER BY id",
                               (scope.conversation, through)).fetchall()

    def memory_extraction_cursor(self, scope: Scope) -> int:
        row = self.db.execute(
            "SELECT through_id FROM memory_extraction_cursors WHERE scope=?",
            (scope.conversation,),
        ).fetchone()
        if row is not None:
            return int(row["through_id"])
        # #72 extracted structured items only when the legacy summary committed. Freeze that
        # migration baseline now so a later summary update cannot skip a failed shadow batch.
        baseline = int(self.summary(scope)[1])
        with self.db:
            self.db.execute(
                """INSERT OR IGNORE INTO memory_extraction_cursors(
                       scope,realm,user_id,through_id
                   ) VALUES (?,?,?,?)""",
                (scope.conversation, scope.realm, str(scope.user_id), baseline),
            )
        return baseline

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
               LEFT JOIN summaries s ON s.scope=t.scope
               WHERE t.id > COALESCE(c.through_id,s.through_id,0)
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
        with self.db:
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
    ):
        exportable = scope.public_at_capture and self.summary_exportable(scope)
        exportable = exportable and all(row["exportable"] for row in self.history(scope))
        if memory_context is None:
            memory_context = CURRENT_MEMORY_CONTEXT.get()
            CURRENT_MEMORY_CONTEXT.set(())
        encoded_context = (
            json.dumps(list(memory_context), ensure_ascii=False, separators=(",", ":"))
            if memory_context
            else ""
        )
        with self.db:
            self.db.execute(
                "INSERT INTO turns(scope,realm,user_id,name,message_id,content,reply,exportable,"
                "turn_id,memory_context) VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            # Bound raw retention even if the summary API keeps failing.
            self.db.execute("DELETE FROM turns WHERE scope=? AND id NOT IN "
                            "(SELECT id FROM turns WHERE scope=? ORDER BY id DESC LIMIT ?)",
                            (scope.conversation, scope.conversation, self.history_turns))

    def save_summary(self, scope: Scope, text: str, through: int):
        exportable = self.summary_exportable(scope) and all(
            row["exportable"] for row in self.pending(scope) if row["id"] <= through)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO summaries("
                "scope,realm,user_id,name,text,through_id,exportable"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    scope.conversation,
                    scope.realm,
                    str(scope.user_id),
                    self.user_name(scope),
                    text,
                    through,
                    int(exportable),
                ),
            )

    def summary_exportable(self, scope: Scope):
        row = self.db.execute("SELECT exportable FROM summaries WHERE scope=?",
                              (scope.conversation,)).fetchone()
        return row is None or bool(row[0])

    @staticmethod
    def _decode_memory_item(row) -> MemoryItem:
        source_ids = json.loads(row["source_message_ids"] or "[]")
        relationship_evidence = RelationshipEvidence.from_mapping(
            json.loads(row["relationship_evidence"] or "{}")
        )
        return MemoryItem(
            id=int(row["id"]),
            user_id=str(row["user_id"]),
            user_name=str(row["user_name"] or ""),
            content=str(row["content"]),
            kind=MemoryKind(row["kind"]),
            origin_realm=str(row["origin_realm"]),
            origin_channel_id=str(row["origin_channel_id"]),
            origin_public_at_capture=bool(row["origin_public_at_capture"]),
            disclosure=MemoryDisclosure(row["disclosure"]),
            source_message_ids=tuple(str(value) for value in source_ids),
            confidence=float(row["confidence"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            relationship_evidence=relationship_evidence,
            status=MemoryStatus(row["status"]),
            superseded_by=(
                int(row["superseded_by"])
                if row["superseded_by"] is not None
                else None
            ),
        )

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
        text = content.strip()
        if not text:
            raise ValueError("Memory item content must not be empty")
        kind = MemoryKind(kind)
        disclosure = MemoryDisclosure(disclosure)
        confidence = float(confidence)
        if not 0 <= confidence <= 1:
            raise ValueError("Memory item confidence must be between 0 and 1")
        if isinstance(relationship_evidence, RelationshipEvidence):
            evidence = relationship_evidence
        else:
            evidence = RelationshipEvidence.from_mapping(relationship_evidence)
        if kind != MemoryKind.RELATIONSHIP and evidence:
            raise ValueError("relationship_evidence is valid only for relationship memory")
        source_ids = tuple(dict.fromkeys(str(value) for value in source_message_ids))
        encoded_sources = json.dumps(source_ids, ensure_ascii=False, separators=(",", ":"))
        encoded_evidence = json.dumps(
            evidence.as_dict(), ensure_ascii=False, separators=(",", ":")
        )
        owner_name = self.user_name(scope) if user_name is None else str(user_name)[:100]
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO memory_items(user_id,user_name,content,kind,origin_realm,origin_channel_id,"
                "origin_public_at_capture,disclosure,source_message_ids,confidence,"
                "relationship_evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(scope.user_id),
                    owner_name,
                    text,
                    kind.value,
                    scope.realm,
                    str(scope.channel_id),
                    int(scope.public_at_capture),
                    disclosure.value,
                    encoded_sources,
                    confidence,
                    encoded_evidence,
                ),
            )
        return int(cursor.lastrowid)

    def memory_items(
        self,
        user_id: int | str,
        *,
        origin_realm: str | None = None,
        include_superseded: bool = False,
    ):
        params: list[str] = [str(user_id)]
        where = "user_id=?"
        if not include_superseded:
            where += " AND status='active'"
        if origin_realm is not None:
            where += " AND origin_realm=?"
            params.append(origin_realm)
        rows = self.db.execute(
            f"SELECT * FROM memory_items WHERE {where} ORDER BY id",
            tuple(params),
        ).fetchall()
        return [self._decode_memory_item(row) for row in rows]

    def memory_reconciliation_candidates(self, scope: Scope, *, limit: int = 24):
        """Return recent candidates without crossing another user's privacy boundary."""

        if scope.guild_id is None:
            rows = self.db.execute(
                """SELECT * FROM memory_items
                   WHERE user_id=? AND status='active'
                   ORDER BY id DESC LIMIT ?""",
                (str(scope.user_id), int(limit)),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT * FROM memory_items
                   WHERE user_id=? AND status='active' AND origin_realm=?
                     AND (origin_public_at_capture=1 OR origin_channel_id=?)
                   ORDER BY id DESC LIMIT ?""",
                (str(scope.user_id), scope.realm, str(scope.channel_id), int(limit)),
            ).fetchall()
        return [self._decode_memory_item(row) for row in reversed(rows)]

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
        if relation not in {"duplicate", "corrects", "conflicts"}:
            raise ValueError("Invalid memory reconciliation relation")
        confidence = float(confidence)
        if not 0 <= confidence <= 1:
            raise ValueError("Memory reconciliation confidence must be between 0 and 1")
        if int(new_memory_item_id) == int(target_memory_item_id):
            raise ValueError("A memory item cannot reconcile with itself")
        owned = self.db.execute(
            """SELECT id,origin_realm,origin_channel_id,origin_public_at_capture
               FROM memory_items
               WHERE id IN (?,?) AND user_id=?""",
            (
                int(new_memory_item_id),
                int(target_memory_item_id),
                str(scope.user_id),
            ),
        ).fetchall()
        by_id = {int(row["id"]): row for row in owned}
        if set(by_id) != {int(new_memory_item_id), int(target_memory_item_id)}:
            raise ValueError("Reconciliation items must belong to the current user")
        new_item = by_id[int(new_memory_item_id)]
        target_item = by_id[int(target_memory_item_id)]
        if (
            str(new_item["origin_realm"]) != scope.realm
            or str(new_item["origin_channel_id"]) != str(scope.channel_id)
        ):
            raise ValueError("New reconciliation item must belong to the current space")
        if scope.guild_id is not None and not (
            str(target_item["origin_realm"]) == scope.realm
            and (
                bool(target_item["origin_public_at_capture"])
                or str(target_item["origin_channel_id"]) == str(scope.channel_id)
            )
        ):
            raise ValueError("Server reconciliation target is outside the disclosure space")
        source_ids = tuple(dict.fromkeys(str(value) for value in source_message_ids))
        encoded_sources = json.dumps(source_ids, ensure_ascii=False, separators=(",", ":"))
        with self.db:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO memory_reconciliation_proposals(
                       user_id,origin_realm,origin_channel_id,new_memory_item_id,
                       target_memory_item_id,relation,confidence,source_message_ids
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    str(scope.user_id),
                    scope.realm,
                    str(scope.channel_id),
                    int(new_memory_item_id),
                    int(target_memory_item_id),
                    relation,
                    confidence,
                    encoded_sources,
                ),
            )
        return int(cursor.lastrowid) if cursor.rowcount else None

    def memory_reconciliation_proposal_id(
        self,
        *,
        new_memory_item_id: int,
        target_memory_item_id: int,
        relation: str,
    ) -> int | None:
        row = self.db.execute(
            """SELECT id FROM memory_reconciliation_proposals
               WHERE new_memory_item_id=? AND target_memory_item_id=? AND relation=?""",
            (int(new_memory_item_id), int(target_memory_item_id), str(relation)),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def memory_reconciliation_proposals(self, user_id: int | str):
        return self.db.execute(
            """SELECT * FROM memory_reconciliation_proposals
               WHERE user_id=? ORDER BY id""",
            (str(user_id),),
        ).fetchall()

    def apply_memory_reconciliation_proposal(
        self,
        proposal_id: int,
        *,
        min_confidence: float = 0.95,
    ) -> str:
        """Apply one conservative lifecycle transition and return its outcome."""

        threshold = float(min_confidence)
        if not 0 <= threshold <= 1:
            raise ValueError("Reconciliation confidence threshold must be between 0 and 1")
        row = self.db.execute(
            """SELECT p.*,
                      n.kind AS new_kind,n.status AS new_status,
                      n.superseded_by AS new_superseded_by,
                      n.origin_realm AS new_origin_realm,
                      n.origin_channel_id AS new_origin_channel_id,
                      t.kind AS target_kind,t.status AS target_status,
                      t.superseded_by AS target_superseded_by,
                      t.origin_realm AS target_origin_realm,
                      t.origin_channel_id AS target_origin_channel_id,
                      t.origin_public_at_capture AS target_public
               FROM memory_reconciliation_proposals p
               JOIN memory_items n
                 ON n.id=p.new_memory_item_id AND n.user_id=p.user_id
               JOIN memory_items t
                 ON t.id=p.target_memory_item_id AND t.user_id=p.user_id
               WHERE p.id=?""",
            (int(proposal_id),),
        ).fetchone()
        if row is None:
            return "missing"
        relation = str(row["relation"])
        if relation == "conflicts":
            return "conflict_deferred"
        if float(row["confidence"]) < threshold:
            return "below_threshold"
        if (
            str(row["new_kind"]) == MemoryKind.RELATIONSHIP.value
            or str(row["target_kind"]) == MemoryKind.RELATIONSHIP.value
        ):
            return "relationship_deferred"
        if str(row["new_kind"]) != str(row["target_kind"]):
            return "kind_mismatch"

        if (
            str(row["new_origin_realm"]) != str(row["origin_realm"])
            or str(row["new_origin_channel_id"]) != str(row["origin_channel_id"])
        ):
            return "invalid_origin"
        if str(row["origin_realm"]).startswith("guild:") and not (
            str(row["target_origin_realm"]) == str(row["origin_realm"])
            and (
                bool(row["target_public"])
                or str(row["target_origin_channel_id"]) == str(row["origin_channel_id"])
            )
        ):
            return "invalid_target_space"

        new_id = int(row["new_memory_item_id"])
        target_id = int(row["target_memory_item_id"])
        new_status = str(row["new_status"])
        target_status = str(row["target_status"])
        new_superseded_by = row["new_superseded_by"]
        target_superseded_by = row["target_superseded_by"]

        if relation == "duplicate":
            if (
                new_status == MemoryStatus.SUPERSEDED.value
                and new_superseded_by is not None
                and int(new_superseded_by) == target_id
            ):
                return "already_applied"
            if new_status != MemoryStatus.ACTIVE.value or target_status != MemoryStatus.ACTIVE.value:
                return "stale_items"
            with self.db:
                cursor = self.db.execute(
                    """UPDATE memory_items
                       SET status='superseded',superseded_by=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND user_id=? AND status='active'""",
                    (target_id, new_id, str(row["user_id"])),
                )
            return "applied" if cursor.rowcount else "stale_items"

        if relation == "corrects":
            if (
                target_status == MemoryStatus.SUPERSEDED.value
                and target_superseded_by is not None
                and int(target_superseded_by) == new_id
            ):
                return "already_applied"
            if new_status != MemoryStatus.ACTIVE.value or target_status != MemoryStatus.ACTIVE.value:
                return "stale_items"
            with self.db:
                cursor = self.db.execute(
                    """UPDATE memory_items
                       SET status='superseded',superseded_by=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND user_id=? AND status='active'""",
                    (new_id, target_id, str(row["user_id"])),
                )
            return "applied" if cursor.rowcount else "stale_items"

        return "unsupported_relation"

    def forget(self, scope: Scope):
        """Delete this user's automatically accumulated memory in the current realm."""
        with self.db:
            for table in (
                "turns",
                "summaries",
                "shared_calls",
                "shared_summaries",
                "memory_extraction_cursors",
            ):
                self.db.execute(f"DELETE FROM {table} WHERE realm=? AND user_id=?",
                                (scope.realm, str(scope.user_id)))
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
                "summaries",
                "shared_calls",
                "shared_summaries",
                "memory_extraction_cursors",
            ):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE scope LIKE ?", (prefix,))
                deleted += self._rowcount(cursor)
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
                "summaries",
                "shared_calls",
                "shared_summaries",
                "memory_extraction_cursors",
            ):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE realm=?", (scope.realm,))
                deleted += self._rowcount(cursor)
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
            for table in (
                "turns",
                "summaries",
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

    def identity_candidates(
        self,
        guild_id: int,
        *,
        exclude_user_ids=(),
        limit: int = 32,
        scan_limit: int = 256,
    ) -> list[dict]:
        """Return bounded recent public speaker identities without fuzzy inference."""

        excluded = {str(value) for value in exclude_user_ids}
        rows = self.db.execute(
            """SELECT scope,user_id,name,id FROM shared_calls
               WHERE realm=?
               ORDER BY id DESC LIMIT ?""",
            (f"guild:{int(guild_id)}", max(int(scan_limit), int(limit))),
        ).fetchall()
        by_user: dict[str, dict] = {}
        for row in rows:
            user_id = str(row["user_id"])
            if user_id in excluded:
                continue
            entry = by_user.get(user_id)
            if entry is None:
                if len(by_user) >= int(limit):
                    continue
                entry = {
                    "user_id": user_id,
                    "names": [],
                    "channel_ids": [],
                    "recent_id": int(row["id"]),
                }
                by_user[user_id] = entry
            name = str(row["name"] or "").strip()
            if name and name not in entry["names"] and len(entry["names"]) < 4:
                entry["names"].append(name[:100])
            parts = str(row["scope"] or "").split(":")
            if len(parts) >= 4 and parts[2] == "channel" and parts[3].isdigit():
                channel_id = int(parts[3])
                if channel_id not in entry["channel_ids"] and len(entry["channel_ids"]) < 4:
                    entry["channel_ids"].append(channel_id)
        return list(by_user.values())

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

    def emoji_rows(self):
        return self.db.execute("SELECT * FROM emoji_registry ORDER BY alias").fetchall()

    def add_emoji(self, alias, emoji_id, description, source_guild_id=None):
        with self.db:
            self.db.execute("INSERT INTO emoji_registry VALUES (?,?,?,?)",
                            (alias, emoji_id, description, source_guild_id))

    def edit_emoji(self, alias, description):
        with self.db:
            return self.db.execute("UPDATE emoji_registry SET description=? WHERE alias=?",
                                   (description, alias)).rowcount > 0

    def remove_emoji(self, alias):
        with self.db:
            return self.db.execute("DELETE FROM emoji_registry WHERE alias=?", (alias,)).rowcount > 0

    def memory_mode_override(self, key: str) -> str | None:
        row = self.db.execute("SELECT mode FROM memory_modes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else None

    def memory_mode_chain(self, scope: Scope) -> dict[str, str | None]:
        global_mode = self.memory_mode_override("global")
        server_mode = self.memory_mode_override(scope.realm) if scope.guild_id is not None else None
        channel_mode = self.memory_mode_override(scope.channel)
        if channel_mode is not None:
            effective, source = channel_mode, "channel"
        elif server_mode is not None:
            effective, source = server_mode, "server"
        elif global_mode is not None:
            effective, source = global_mode, "global"
        else:
            effective, source = "normal", "default"
        return {
            "global": global_mode,
            "server": server_mode,
            "channel": channel_mode,
            "effective": effective,
            "source": source,
        }

    def memory_mode(self, scope: Scope) -> str:
        return str(self.memory_mode_chain(scope)["effective"])

    def set_memory_mode_override(self, key: str, mode: str | None):
        if mode is not None and mode not in {"normal", "read_only", "write_only", "off"}:
            raise ValueError("Invalid memory mode")
        with self.db:
            if mode is None:
                self.db.execute("DELETE FROM memory_modes WHERE scope=?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO memory_modes VALUES (?,?)", (key, mode))

    def set_memory_mode(self, scope: Scope, mode: str):
        """Backward-compatible channel override setter."""
        self.set_memory_mode_override(scope.channel, mode)

    def memory_mode_overrides(self) -> dict[str, str]:
        rows = self.db.execute("SELECT scope,mode FROM memory_modes ORDER BY scope").fetchall()
        return {row["scope"]: row["mode"] for row in rows}

    def chat_log_mode_override(self, key: str) -> str | None:
        row = self.db.execute("SELECT mode FROM chat_log_modes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else None

    def chat_log_mode_chain(self, scope: Scope) -> dict[str, str | None]:
        global_mode = self.chat_log_mode_override("global")
        server_mode = self.chat_log_mode_override(scope.realm) if scope.guild_id is not None else None
        channel_mode = self.chat_log_mode_override(scope.channel)
        if channel_mode is not None:
            effective, source = channel_mode, "channel"
        elif server_mode is not None:
            effective, source = server_mode, "server"
        elif global_mode is not None:
            effective, source = global_mode, "global"
        else:
            effective, source = "on", "default"
        return {
            "global": global_mode,
            "server": server_mode,
            "channel": channel_mode,
            "effective": effective,
            "source": source,
        }

    def chat_log_enabled(self, scope: Scope) -> bool:
        return self.chat_log_mode_chain(scope)["effective"] == "on"

    def set_chat_log_mode_override(self, key: str, mode: str | None):
        if mode is not None and mode not in {"on", "off"}:
            raise ValueError("Invalid chat log mode")
        with self.db:
            if mode is None:
                self.db.execute("DELETE FROM chat_log_modes WHERE scope=?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO chat_log_modes VALUES (?,?)", (key, mode))

    def chat_log_mode_overrides(self) -> dict[str, str]:
        rows = self.db.execute("SELECT scope,mode FROM chat_log_modes ORDER BY scope").fetchall()
        return {row["scope"]: row["mode"] for row in rows}
