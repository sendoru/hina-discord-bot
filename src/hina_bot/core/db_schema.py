"""Current schema only: fixed validation and explicit fresh creation.

No legacy inspection, ALTER, data conversion, or tooling import belongs here.
The offline preparer registers baselines and applies ordered version upgrades.
"""

import hashlib
import json

from .db_ddl import DDL, LEDGER_DDL, V1_SIGNATURES, V1_TABLES

COMPONENTS = tuple(DDL)
HISTORY = {component: (component + "-baseline-v1",) for component in COMPONENTS}
SCHEMA_SIGNATURES = {component: {1: V1_SIGNATURES[component]} for component in COMPONENTS}


class SchemaError(RuntimeError):
    pass


def schema_signature(db, tables):
    """Exact owned metadata, including unexpected attached indexes/triggers.

    No SQL normalization: literals, constraints and column order remain intact.
    Unrelated maintenance tables are outside this component's ownership.
    """
    slots = ",".join("?" for _ in tables)
    rows = [tuple(row) for row in db.execute(
        f"SELECT type,name,tbl_name,sql FROM sqlite_master "
        f"WHERE tbl_name IN ({slots}) OR name IN ({slots}) ORDER BY type,name",
        (*tables, *tables),
    )]
    return hashlib.sha256(json.dumps(
        rows, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


def schema_versions(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
        return {}
    if schema_signature(db, ("schema_migrations",)) not in V1_SIGNATURES["ledger"]:
        raise SchemaError("Unsupported schema_migrations table")
    versions = {}
    for component, version, name in db.execute(
        "SELECT component,version,name FROM schema_migrations ORDER BY component,version"
    ):
        if (
            component not in HISTORY
            or version != versions.get(component, 0) + 1
            or version > len(HISTORY[component])
            or name != HISTORY[component][version - 1]
        ):
            raise SchemaError("Unknown, future or incomplete schema migration history")
        versions[component] = version
    return versions


def validate_schema(db, components=COMPONENTS, *, versions=None):
    for component in components:
        if component not in COMPONENTS:
            raise ValueError("Unknown schema component")
        version = versions[component] if versions is not None else len(HISTORY[component])
        if schema_signature(db, V1_TABLES[component]) not in SCHEMA_SIGNATURES[component][version]:
            raise SchemaError(f"Unsupported {component} schema; use hina-upgrade-legacy-db offline")


def require_current(db, components=COMPONENTS):
    versions = schema_versions(db)
    if any(versions.get(component) != len(HISTORY[component]) for component in components):
        raise SchemaError("Database preparation required; run hina-prepare-db --dry-run first")
    validate_schema(db, components)


def record_versions(db, component, start=1):
    """Borrow the preparer's/fresh creator's transaction; never commit here."""
    db.execute(LEDGER_DDL.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
    for version in range(start, len(HISTORY[component]) + 1):
        db.execute(
            "INSERT INTO schema_migrations(component,version,name) VALUES (?,?,?)",
            (component, version, HISTORY[component][version - 1]),
        )


def create_tables(db, components):
    for component in components:
        for statement in DDL[component]:
            db.execute(statement)
    if "store" in components:
        # A fresh v1 starts with unified settings; this is not a conversion.
        db.execute("INSERT INTO notes(scope,text) VALUES ('config:chatlog_unified_v1','1')")


def initialize_connection(db, components=COMPONENTS):
    """Create fresh areas only. Existing tables must use offline preparation."""
    if db.in_transaction:
        raise RuntimeError("Schema creation requires its own transaction")
    if any(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
           for component in components for name in V1_TABLES[component]):
        raise SchemaError("Fresh creation cannot adopt or upgrade existing tables")
    db.execute("BEGIN IMMEDIATE")
    try:
        create_tables(db, components)
        for component in components:
            record_versions(db, component)
        require_current(db, components)
        db.commit()
    except BaseException:
        db.rollback()
        raise
