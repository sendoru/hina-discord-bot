"""Existing Store schema bootstrap and compatibility migrations.

Keep statement order and transaction boundaries stable. Opening a Store still
runs this bootstrap; read-only opening and versioned migrations are later work.
"""

import sqlite3

from .admin_commands import ensure_admin_command_schema


def initialize_store_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
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
            context_provenance TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS turns_scope ON turns(scope, id);
        CREATE INDEX IF NOT EXISTS turns_owner ON turns(realm, user_id);
        CREATE TABLE IF NOT EXISTS failed_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL, realm TEXT NOT NULL, user_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL,
            content TEXT NOT NULL,
            reply TEXT NOT NULL DEFAULT '',
            turn_id TEXT,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            reply_delivered INTEGER NOT NULL CHECK(reply_delivered IN (0,1)),
            error_type TEXT NOT NULL DEFAULT '',
            error_fingerprint TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS failed_turns_scope ON failed_turns(scope, id);
        CREATE INDEX IF NOT EXISTS failed_turns_turn_id ON failed_turns(turn_id);
        CREATE INDEX IF NOT EXISTS failed_turns_message_id ON failed_turns(message_id);
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
                CHECK(status IN ('active','superseded','retracted')),
            superseded_by INTEGER,
            revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(superseded_by IS NULL OR superseded_by != id)
        );
        CREATE INDEX IF NOT EXISTS memory_items_owner ON memory_items(user_id, id);
        CREATE INDEX IF NOT EXISTS memory_items_origin
            ON memory_items(origin_realm, origin_channel_id, user_id);
        CREATE TABLE IF NOT EXISTS memory_item_edit_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_item_id INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            content TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN (
                'fact','event','preference','relationship','boundary','task'
            )),
            disclosure TEXT NOT NULL CHECK(disclosure IN (
                'local','implicit','reference_gated','global'
            )),
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            relationship_evidence TEXT NOT NULL DEFAULT '{}',
            edited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            admin_command_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS memory_item_edit_history_item
            ON memory_item_edit_history(memory_item_id, revision, id);
        CREATE TABLE IF NOT EXISTS memory_extraction_cursors (
            scope TEXT PRIMARY KEY,
            realm TEXT NOT NULL,
            user_id TEXT NOT NULL,
            through_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS memory_extraction_cursor_owner
            ON memory_extraction_cursors(realm, user_id);
        CREATE TABLE IF NOT EXISTS memory_extraction_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            start_id INTEGER NOT NULL,
            through_id INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, through_id)
        );
        CREATE INDEX IF NOT EXISTS memory_extraction_batch_scope
            ON memory_extraction_batches(scope, id);
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
        CREATE TABLE IF NOT EXISTS guild_metadata (
            guild_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS channel_metadata (
            channel_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS channel_metadata_guild
            ON channel_metadata(guild_id, channel_id);
        CREATE TABLE IF NOT EXISTS notes (scope TEXT PRIMARY KEY, text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversation_identity_names (
            scope TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
    """)
    ensure_admin_command_schema(db)
    db.commit()
    columns = {row["name"] for row in db.execute("PRAGMA table_info(turns)")}
    if "memory_context" not in columns:
        with db:
            db.execute(
                "ALTER TABLE turns ADD COLUMN memory_context TEXT NOT NULL DEFAULT ''"
            )
    if "context_provenance" not in columns:
        with db:
            db.execute(
                "ALTER TABLE turns ADD COLUMN context_provenance TEXT NOT NULL DEFAULT ''"
            )
    if "turn_id" not in columns:
        with db:
            db.execute("ALTER TABLE turns ADD COLUMN turn_id TEXT")
    if "name" not in columns:
        with db:
            db.execute("ALTER TABLE turns ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    with db:
        db.execute("CREATE INDEX IF NOT EXISTS turns_turn_id ON turns(turn_id)")
    legacy_summary_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='summaries'"
    ).fetchone()
    if legacy_summary_table is not None:
        # Preserve the one migration-relevant field before removing the obsolete
        # personal-summary store. Existing structured cursors always win.
        with db:
            db.execute(
                """INSERT OR IGNORE INTO memory_extraction_cursors(
                       scope,realm,user_id,through_id
                   )
                   SELECT scope,realm,user_id,through_id FROM summaries"""
            )
            db.execute("DROP TABLE summaries")
    memory_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(memory_items)")
    }
    if "relationship_evidence" not in memory_columns:
        with db:
            db.execute(
                "ALTER TABLE memory_items "
                "ADD COLUMN relationship_evidence TEXT NOT NULL DEFAULT '{}'"
            )
    if "user_name" not in memory_columns:
        with db:
            db.execute(
                "ALTER TABLE memory_items ADD COLUMN user_name TEXT NOT NULL DEFAULT ''"
            )
    if "status" not in memory_columns:
        with db:
            db.execute(
                "ALTER TABLE memory_items ADD COLUMN status TEXT NOT NULL DEFAULT 'active' "
                "CHECK(status IN ('active','superseded','retracted'))"
            )
    if "superseded_by" not in memory_columns:
        with db:
            db.execute("ALTER TABLE memory_items ADD COLUMN superseded_by INTEGER")
    if "revision" not in memory_columns:
        with db:
            db.execute(
                "ALTER TABLE memory_items ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
            )

    memory_schema = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_items'"
    ).fetchone()
    memory_schema_sql = str(memory_schema["sql"] or "") if memory_schema is not None else ""
    if "'retracted'" not in memory_schema_sql:
        # SQLite cannot widen an existing CHECK constraint in place. Preserve the
        # AUTOINCREMENT high-water mark as well as rows and provenance: copying only
        # surviving IDs would otherwise allow a previously deleted ID to be reused.
        with db:
            previous_sequence = db.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='memory_items'"
            ).fetchone()
            db.execute("ALTER TABLE memory_items RENAME TO memory_items_pre_retract")
            db.execute(
                """CREATE TABLE memory_items (
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
                       confidence REAL NOT NULL DEFAULT 1.0
                           CHECK(confidence >= 0 AND confidence <= 1),
                       relationship_evidence TEXT NOT NULL DEFAULT '{}',
                       status TEXT NOT NULL DEFAULT 'active'
                           CHECK(status IN ('active','superseded','retracted')),
                       superseded_by INTEGER,
                       revision INTEGER NOT NULL DEFAULT 0,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       CHECK(superseded_by IS NULL OR superseded_by != id)
                   )"""
            )
            db.execute(
                """INSERT INTO memory_items(
                       id,user_id,user_name,content,kind,origin_realm,origin_channel_id,
                       origin_public_at_capture,disclosure,source_message_ids,confidence,
                       relationship_evidence,status,superseded_by,revision,created_at,updated_at
                   )
                   SELECT
                       id,user_id,user_name,content,kind,origin_realm,origin_channel_id,
                       origin_public_at_capture,disclosure,source_message_ids,confidence,
                       relationship_evidence,status,superseded_by,revision,created_at,updated_at
                   FROM memory_items_pre_retract"""
            )
            db.execute("DROP TABLE memory_items_pre_retract")
            if previous_sequence is not None:
                # A newly rebuilt AUTOINCREMENT table takes MAX(surviving IDs).
                # Restore the old high-water mark before any new item is inserted.
                db.execute(
                    "DELETE FROM sqlite_sequence WHERE name='memory_items'"
                )
                db.execute(
                    """INSERT INTO sqlite_sequence(name,seq)
                       SELECT 'memory_items', MAX(?, COALESCE(MAX(id), 0))
                       FROM memory_items""",
                    (int(previous_sequence["seq"]),),
                )
            db.execute(
                "CREATE INDEX memory_items_owner ON memory_items(user_id,id)"
            )
            db.execute(
                """CREATE INDEX memory_items_origin
                   ON memory_items(origin_realm,origin_channel_id,user_id)"""
            )
    with db:
        db.execute(
            "CREATE INDEX IF NOT EXISTS memory_items_owner_status "
            "ON memory_items(user_id,status,id)"
        )
