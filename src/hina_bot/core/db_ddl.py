"""Target SQLite DDL for the three fixed schema areas.

No connection opening, migration execution or commits live here. Runtime uses
fixed v1 metadata signatures; legacy upgrades live only in the offline tool.
"""

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
    """CREATE TABLE IF NOT EXISTS shared_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
            realm TEXT NOT NULL, user_id TEXT NOT NULL, message_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
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
    """CREATE TABLE IF NOT EXISTS memory_extraction_cursors (
            scope TEXT PRIMARY KEY,
            realm TEXT NOT NULL,
            user_id TEXT NOT NULL,
            through_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    """CREATE TABLE IF NOT EXISTS memory_extraction_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            start_id INTEGER NOT NULL,
            through_id INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, through_id)
        )""",
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
    """CREATE TABLE IF NOT EXISTS notes (scope TEXT PRIMARY KEY, text TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS conversation_identity_names (
            scope TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )""",
)

# Installed after known legacy columns are added; also used for validation and
# index restoration after table rebuilds. Each index has one canonical definition.
STORE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS turns_scope ON turns(scope, id)",
    "CREATE INDEX IF NOT EXISTS turns_owner ON turns(realm, user_id)",
    "CREATE INDEX IF NOT EXISTS turns_turn_id ON turns(turn_id)",
    "CREATE INDEX IF NOT EXISTS failed_turns_scope ON failed_turns(scope, id)",
    "CREATE INDEX IF NOT EXISTS failed_turns_turn_id ON failed_turns(turn_id)",
    "CREATE INDEX IF NOT EXISTS failed_turns_message_id ON failed_turns(message_id)",
    "CREATE INDEX IF NOT EXISTS shared_calls_scope ON shared_calls(scope, id)",
    "CREATE INDEX IF NOT EXISTS memory_items_owner ON memory_items(user_id, id)",
    """CREATE INDEX IF NOT EXISTS memory_items_origin
       ON memory_items(origin_realm, origin_channel_id, user_id)""",
    "CREATE INDEX IF NOT EXISTS memory_items_owner_status ON memory_items(user_id,status,id)",
    """CREATE INDEX IF NOT EXISTS memory_item_edit_history_item
       ON memory_item_edit_history(memory_item_id, revision, id)""",
    """CREATE INDEX IF NOT EXISTS memory_extraction_cursor_owner
       ON memory_extraction_cursors(realm, user_id)""",
    "CREATE INDEX IF NOT EXISTS memory_extraction_batch_scope ON memory_extraction_batches(scope, id)",
    """CREATE INDEX IF NOT EXISTS memory_reconciliation_owner
       ON memory_reconciliation_proposals(user_id, origin_realm, origin_channel_id, id)""",
    "CREATE INDEX IF NOT EXISTS channel_metadata_guild ON channel_metadata(guild_id, channel_id)",
)

SETTINGS_DDL = (
    """CREATE TABLE IF NOT EXISTS runtime_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
    """CREATE TABLE IF NOT EXISTS runtime_config_startup (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
)

ADMIN_DDL = (
    """CREATE TABLE IF NOT EXISTS instructions (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                created_at TEXT,
                updated_at TEXT
            )""",
    """CREATE TABLE IF NOT EXISTS runtime_knowledge (
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
            )""",
    """CREATE INDEX IF NOT EXISTS runtime_knowledge_enabled_kind
                ON runtime_knowledge(enabled,kind)""",
    """CREATE TABLE IF NOT EXISTS admin_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
)

QUEUE_DDL = (
    """CREATE TABLE IF NOT EXISTS admin_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL UNIQUE,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','running','succeeded','failed')),
            result_json TEXT NOT NULL DEFAULT '{}',
            error_type TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT
        )""",
    """CREATE INDEX IF NOT EXISTS admin_commands_status
            ON admin_commands(status, id)""",
)

LEDGER_DDL = """CREATE TABLE schema_migrations (
    component TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(component,version))"""

DDL = {
    "store": STORE_DDL + STORE_INDEXES + QUEUE_DDL,
    "settings": SETTINGS_DDL,
    "admin": ADMIN_DDL,
}


# Fixed v1 ownership and exact sqlite_master signatures. The second spelling,
# where present, is the inspected operational schema after historical ALTERs.
# These contain schema metadata only; no database rows or identifiers.
V1_TABLES = {
    'store': (
        'admin_commands',
        'channel_metadata',
        'chat_log_modes',
        'conversation_identity_names',
        'emoji_registry',
        'failed_turns',
        'guild_metadata',
        'memory_extraction_batches',
        'memory_extraction_cursors',
        'memory_item_edit_history',
        'memory_items',
        'memory_modes',
        'memory_reconciliation_proposals',
        'notes',
        'shared_calls',
        'shared_summaries',
        'turns',
    ),
    'settings': (
        'runtime_config',
        'runtime_config_startup',
    ),
    'admin': (
        'admin_migrations',
        'instructions',
        'runtime_knowledge',
    ),
}

V1_SIGNATURES = {
    'store': {
        '8287491c5024df5fb6e7026ed48985a3b0f26142c2f1b66b9421d3d04b7ef6eb',
        'cfa8c973864b6c2d27d1ed6f4113b439851753c1f15c323081239125de1ffb4c',
    },
    'settings': {
        '59919ee407c53fc9cfd95cf9e9ec7753def51d05f2c5ec67a4bc06610b0f41e8',
    },
    'admin': {
        'bfbc0142bacd3806691e2a1927a375e37737907010f1c1adc700aca96a5e8407',
    },
    "ledger": {'0b11af7999ed698fe5a9e14a977d9a434603a9880d63e00a69a3b46fa36da0e8'},
}
