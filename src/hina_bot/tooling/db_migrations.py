"""Offline baseline registration and explicit, ordered Hina schema upgrades.

Only baseline v1 ships today. Add a named function to UPGRADES for each real
next version, plus its HISTORY entry/current DDL/metadata signature. No schema
repair or legacy format inference is performed by this path.
"""

from hina_bot.core.db_ddl import LEDGER_DDL, V1_TABLES
from hina_bot.core.db_schema import (
    COMPONENTS,
    HISTORY,
    SchemaError,
    create_tables,
    record_versions,
    require_current,
    schema_versions,
    validate_schema,
)

from .maintenance_backup import database_signature

# Explicit (component, next_version, function) steps; no v2 feature is implemented.
UPGRADES = ()


def inspect_connection(db, components=COMPONENTS):
    components = tuple(components)
    if set(components) - set(COMPONENTS):
        raise ValueError("Unknown schema component")
    versions = schema_versions(db)
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT GLOB 'sqlite_*'"
    )}
    known = set().union(*V1_TABLES.values()) | {"schema_migrations"}
    if tables and not tables & known:
        raise SchemaError("Not a recognized Hina database; refuse schema adoption")
    if "store" in components and "summaries" in tables:
        raise SchemaError("Legacy database; run hina-upgrade-legacy-db offline")
    pending = []
    for component in COMPONENTS:
        if component not in components:
            continue
        present = tables & set(V1_TABLES[component])
        if present or component in versions:
            validate_schema(db, (component,), versions={component: versions.get(component, 1)})
        if component not in versions:
            if present and component == "store":
                marker = db.execute("SELECT text FROM notes WHERE scope='config:chatlog_unified_v1'").fetchone()
                if marker is None or marker[0] != "1":
                    raise SchemaError("Legacy chatlog settings; run hina-upgrade-legacy-db offline")
            if present and component == "settings" and db.execute(
                "SELECT 1 FROM runtime_config WHERE key='retrieval_v2_mode'"
            ).fetchone():
                raise SchemaError("Legacy RAG settings; run hina-upgrade-legacy-db offline")
        if versions.get(component, 0) < len(HISTORY[component]):
            pending.append(component)
    return tuple(pending)


def prepare_connection(db, components=COMPONENTS, *, expected_signature=None):
    if db.in_transaction:
        raise RuntimeError("Schema preparation requires its own transaction")
    pending = inspect_connection(db, components)
    if not pending:
        if expected_signature is not None and database_signature(db) != expected_signature:
            raise SchemaError("Database changed since backup; refuse preparation")
        return ()
    db.execute("BEGIN IMMEDIATE")
    try:
        if expected_signature is not None and database_signature(db) != expected_signature:
            raise SchemaError("Database changed since backup; refuse preparation")
        if inspect_connection(db, components) != pending:
            raise SchemaError("Schema changed since inspection")
        versions = schema_versions(db)
        for component in pending:
            version = versions.get(component, 0)
            if version == 0:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                                  (V1_TABLES[component][0],)).fetchone():
                    create_tables(db, (component,))
                    record_versions(db, component)
                    continue
                # Register the known current v1; do not replay legacy conversions.
                db.execute(LEDGER_DDL.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
                db.execute("INSERT INTO schema_migrations(component,version,name) VALUES (?,?,?)",
                           (component, 1, HISTORY[component][0]))
                version = 1
            for target in range(version + 1, len(HISTORY[component]) + 1):
                step = next((apply for area, number, apply in UPGRADES
                             if (area, number) == (component, target)), None)
                if step is None:
                    raise SchemaError(f"Missing explicit migration: {component} v{target}")
                step(db)
                db.execute("INSERT INTO schema_migrations(component,version,name) VALUES (?,?,?)",
                           (component, target, HISTORY[component][target - 1]))
        require_current(db, components)
        db.commit()
        return pending
    except BaseException:
        db.rollback()
        raise
