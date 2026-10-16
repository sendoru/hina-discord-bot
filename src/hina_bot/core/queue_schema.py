"""Explicit schema statements; caller owns the transaction."""

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
