"""Explicit schema statements; caller owns the transaction."""

STORE_DDL = (
    """CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL, realm TEXT NOT NULL, user_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL, reply TEXT NOT NULL, exportable INTEGER NOT NULL,
            turn_id TEXT,
            memory_context TEXT NOT NULL DEFAULT '',
            context_provenance TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    """CREATE INDEX IF NOT EXISTS turns_scope ON turns(scope, id)""",
    """CREATE INDEX IF NOT EXISTS turns_owner ON turns(realm, user_id)""",
    """CREATE TABLE IF NOT EXISTS failed_turns (
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
        )""",
    """CREATE INDEX IF NOT EXISTS failed_turns_scope ON failed_turns(scope, id)""",
    """CREATE INDEX IF NOT EXISTS failed_turns_turn_id ON failed_turns(turn_id)""",
    """CREATE INDEX IF NOT EXISTS failed_turns_message_id ON failed_turns(message_id)""",
    """CREATE TABLE IF NOT EXISTS shared_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
            realm TEXT NOT NULL, user_id TEXT NOT NULL, message_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    """CREATE INDEX IF NOT EXISTS shared_calls_scope ON shared_calls(scope, id)""",
    """CREATE TABLE IF NOT EXISTS shared_summaries (
            scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
            name TEXT NOT NULL, text TEXT NOT NULL, through_id INTEGER NOT NULL
        )""",
    """CREATE TABLE IF NOT EXISTS memory_items (
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
        )""",
    """CREATE INDEX IF NOT EXISTS memory_items_owner ON memory_items(user_id, id)""",
    """CREATE INDEX IF NOT EXISTS memory_items_origin
            ON memory_items(origin_realm, origin_channel_id, user_id)""",
    """CREATE TABLE IF NOT EXISTS memory_item_edit_history (
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
        )""",
    """CREATE INDEX IF NOT EXISTS memory_item_edit_history_item
            ON memory_item_edit_history(memory_item_id, revision, id)""",
    """CREATE TABLE IF NOT EXISTS memory_extraction_cursors (
            scope TEXT PRIMARY KEY,
            realm TEXT NOT NULL,
            user_id TEXT NOT NULL,
            through_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    """CREATE INDEX IF NOT EXISTS memory_extraction_cursor_owner
            ON memory_extraction_cursors(realm, user_id)""",
    """CREATE TABLE IF NOT EXISTS memory_extraction_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            start_id INTEGER NOT NULL,
            through_id INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, through_id)
        )""",
    """CREATE INDEX IF NOT EXISTS memory_extraction_batch_scope
            ON memory_extraction_batches(scope, id)""",
    """CREATE TABLE IF NOT EXISTS memory_reconciliation_proposals (
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
        )""",
    """CREATE INDEX IF NOT EXISTS memory_reconciliation_owner
            ON memory_reconciliation_proposals(user_id, origin_realm, origin_channel_id, id)""",
    """CREATE TABLE IF NOT EXISTS emoji_registry (
            alias TEXT PRIMARY KEY, emoji_id TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
            source_guild_id TEXT
        )""",
    """CREATE TABLE IF NOT EXISTS memory_modes (
            scope TEXT PRIMARY KEY,
            mode TEXT NOT NULL CHECK(mode IN ('normal','read_only','write_only','off'))
        )""",
    """CREATE TABLE IF NOT EXISTS chat_log_modes (
            scope TEXT PRIMARY KEY,
            mode TEXT NOT NULL CHECK(mode IN ('on','off'))
        )""",
    """CREATE TABLE IF NOT EXISTS guild_metadata (
            guild_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    """CREATE TABLE IF NOT EXISTS channel_metadata (
            channel_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    """CREATE INDEX IF NOT EXISTS channel_metadata_guild
            ON channel_metadata(guild_id, channel_id)""",
    """CREATE TABLE IF NOT EXISTS notes (scope TEXT PRIMARY KEY, text TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS conversation_identity_names (
            scope TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )""",
)
