"""Explicit schema statements; caller owns the transaction."""

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
