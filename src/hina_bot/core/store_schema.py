"""Known Store DDL/data changes. Caller owns BEGIN, commit and rollback."""

import sqlite3

from .queue_schema import QUEUE_DDL
from .store_ddl import STORE_DDL, STORE_INDEXES


def initialize_store_schema(db: sqlite3.Connection) -> None:
    for statement in STORE_DDL + QUEUE_DDL:
        db.execute(statement)
    columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
    for name, declaration in (
        ("memory_context", "TEXT NOT NULL DEFAULT ''"),
        ("context_provenance", "TEXT NOT NULL DEFAULT ''"),
        ("turn_id", "TEXT"),
        ("name", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE turns ADD COLUMN {name} {declaration}")
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='summaries'").fetchone():
        # Existing structured cursors still win; the complete legacy source is in
        # the verified pre-migration backup, not silently reconstructed later.
        db.execute("""INSERT OR IGNORE INTO memory_extraction_cursors(scope,realm,user_id,through_id)
                      SELECT scope,realm,user_id,through_id FROM summaries""")
        db.execute("DROP TABLE summaries")
    columns = {row[1] for row in db.execute("PRAGMA table_info(memory_items)")}
    for name, declaration in (
        ("relationship_evidence", "TEXT NOT NULL DEFAULT '{}'"),
        ("user_name", "TEXT NOT NULL DEFAULT ''"),
        (
            "status",
            "TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded','retracted'))",
        ),
        ("superseded_by", "INTEGER"),
        ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE memory_items ADD COLUMN {name} {declaration}")
    # Widening known legacy constraints is handled by db_schema's verified copy,
    # preserving every ID, provenance field, revision and AUTOINCREMENT high mark.
    for statement in STORE_INDEXES:
        db.execute(statement)
