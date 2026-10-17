"""Explicit, transactional preparation of the three existing SQLite schema areas.

Inspection never writes. prepare_connection borrows a connection, owns one
transaction, and must be called only after the file caller has verified a backup.
No runtime Store/Registry constructor calls it.
"""

import hashlib
import json
import re
import sqlite3
from functools import cache

from .admin_schema import ADMIN_DDL
from .chatlog_modes import _MIGRATION_MARKER, legacy_chatlog_plan
from .config import LEGACY_RAG_MODES
from .queue_schema import QUEUE_DDL
from .scope_overrides import CHATLOG_CAPTURE_NOTE_PREFIX
from .settings_schema import (
    SETTINGS_DDL,
    migrate_legacy_rag_override,
    replace_legacy_chatlog_settings,
)
from .store_ddl import STORE_DDL, STORE_INDEXES
from .store_schema import initialize_store_schema

COMPONENTS = ("store", "settings", "admin")
DDL = {
    "store": STORE_DDL + STORE_INDEXES + QUEUE_DDL,
    "settings": SETTINGS_DDL,
    "admin": ADMIN_DDL,
}
LEGACY_OPTIONAL = {
    "turns": {"name", "turn_id", "memory_context", "context_provenance"},
    "memory_items": {"relationship_evidence", "user_name", "status", "superseded_by", "revision"},
}
# These schemas existed before CHECK constraints were installed. Copy all columns,
# preserving IDs and sequences, rather than blessing a weaker schema as current.
LEGACY_REBUILD = {"memory_items", "memory_item_edit_history", "memory_reconciliation_proposals"}
LEDGER_DDL = """CREATE TABLE schema_migrations (
    component TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(component,version))"""


class SchemaError(RuntimeError):
    pass


def _sql(db, name):
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE name=? AND type IN ('table','view','index')", (name,)
    ).fetchone()
    return row[0] if row else None


def _columns(db, table):
    return {row[1]: tuple(row[1:]) for row in db.execute(f'PRAGMA table_xinfo("{table}")')}


def _checks(sql):
    # Owned DDL uses only these shallow CHECK expressions (including IN lists).
    # SQL keywords are case-insensitive; quoted values are not. Do not bless an
    # uppercase-only lifecycle CHECK as equivalent to the lowercase contract.
    parts = re.split(r"('(?:''|[^'])*')", sql)
    value = "".join(
        part if index % 2 else re.sub(r"\s+", "", part).lower() for index, part in enumerate(parts)
    )
    checks = re.findall(r"check\(([^()]*(?:\([^()]*\)[^()]*)*)\)", value)
    if len(checks) != value.count("check("):
        raise SchemaError("Unsupported CHECK expression")
    return frozenset(checks)


def _unique_keys(db, table):
    return frozenset(
        tuple(
            (x[2], x[3], x[4])
            for x in db.execute('PRAGMA index_xinfo("' + row[1].replace('"', '""') + '")')
            if x[5]
        )
        for row in db.execute(f'PRAGMA index_list("{table}")')
        if row[2]
    )


@cache
def _expected():
    db = sqlite3.connect(":memory:")
    try:
        result = {}
        for component in COMPONENTS:
            for statement in DDL[component]:
                db.execute(statement)
            tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", "\n".join(DDL[component]))
            result[component] = {
                table: (
                    _columns(db, table),
                    _checks(_sql(db, table)),
                    _unique_keys(db, table),
                    _sql(db, table),
                )
                for table in tables
            }
        indexes = {
            r[0]: (r[1], r[2])
            for r in db.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            )
        }
        return result, indexes
    finally:
        db.close()


def _matches(db, table, spec):
    return (
        _columns(db, table) == spec[0]
        and _checks(_sql(db, table)) == spec[1]
        and _unique_keys(db, table) == spec[2]
    )


def inspect_connection(db, components=COMPONENTS) -> tuple[str, ...]:
    """Validate owned structures and return ordered areas needing explicit preparation."""
    components = tuple(components)
    if set(components) - set(COMPONENTS):
        raise ValueError("Unknown schema component")
    expected, indexes = _expected()
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'"
        )
    }
    known = set().union(*(set(area) for area in expected.values())) | {
        "summaries",
        "schema_migrations",
        "conversation_reset_epochs",
        "observability_epochs",
    }
    if tables and not tables & known:
        raise SchemaError("Not a recognized Hina database; refuse schema adoption")
    ledger = {}
    if _sql(db, "schema_migrations"):
        ledger_columns = {
            "component": ("component", "TEXT", 1, None, 1, 0),
            "version": ("version", "INTEGER", 1, None, 2, 0),
            "name": ("name", "TEXT", 1, None, 0, 0),
            "applied_at": ("applied_at", "TEXT", 1, "CURRENT_TIMESTAMP", 0, 0),
        }
        if (
            _columns(db, "schema_migrations") != ledger_columns
            or _checks(_sql(db, "schema_migrations"))
            or _unique_keys(db, "schema_migrations")
            != {
                (("component", 0, "BINARY"), ("version", 0, "BINARY")),
            }
        ):
            raise SchemaError("Unsupported schema_migrations table")
        for component, version, name in db.execute(
            "SELECT component,version,name FROM schema_migrations"
        ):
            if component not in COMPONENTS or version != 1 or name != component + "-baseline-v1":
                raise SchemaError("Unknown or future schema migration history")
            if component in ledger:
                raise SchemaError("Duplicate schema migration history")
            ledger[component] = version
    pending = []
    for component in COMPONENTS:
        if component not in components:
            continue
        current = True
        for table, spec in expected[component].items():
            sql = _sql(db, table)
            if sql is None:
                current = False
                continue
            if not re.match(r"CREATE\s+TABLE\b", sql, re.IGNORECASE):
                raise SchemaError(f"Owned schema object is not a table: {table}")
            if re.search(
                r"\b(?:COLLATE|DESC|STRICT|GENERATED)\b|\bON\s+CONFLICT\b|"
                r"\bWITHOUT\s+ROWID\b",
                sql,
                re.IGNORECASE,
            ):
                raise SchemaError(f"Unsupported table semantics in {table}")
            cols = _columns(db, table)
            optional = LEGACY_OPTIONAL.get(table, set())
            if set(cols) - set(spec[0]) or (set(spec[0]) - set(cols)) - optional:
                raise SchemaError(f"Unsupported columns in {table}")
            for name, column in cols.items():
                # Column position is deliberately excluded: ALTER appends columns.
                if column != spec[0][name]:
                    raise SchemaError(f"Unsupported column definition: {table}.{name}")
            checks = _checks(sql)
            allowed = spec[1]
            if table == "memory_items":
                allowed |= {"statusin('active','superseded')"}
            if checks - allowed or _unique_keys(db, table) - spec[2]:
                raise SchemaError(f"Unsupported constraints in {table}")
            if db.execute(f'PRAGMA foreign_key_list("{table}")').fetchone():
                raise SchemaError(f"Unsupported foreign keys in {table}")
            if "autoincrement" in spec[3].lower() and "autoincrement" not in sql.lower():
                raise SchemaError(f"Missing AUTOINCREMENT in {table}")
            exact = _matches(db, table, spec)
            if not exact:
                if table not in LEGACY_REBUILD and not set(spec[0]) - set(cols):
                    raise SchemaError(f"Unsupported constraints in {table}")
                # Rebuilds may discard dependent objects; refuse anything we cannot restore.
                if table in LEGACY_REBUILD:
                    for kind, name in db.execute(
                        "SELECT type,name FROM sqlite_master WHERE tbl_name=? "
                        "AND type IN ('trigger','index') AND sql IS NOT NULL",
                        (table,),
                    ):
                        if kind == "trigger" or name not in indexes:
                            raise SchemaError(f"Unsupported rebuild dependency: {name}")
                current = False
        for name, (table, sql) in indexes.items():
            if table not in expected[component]:
                continue
            found = _sql(db, name)
            if found is None:
                current = False
            elif re.sub(r"\s+", "", found.lower()).replace("ifnotexists", "") != re.sub(
                r"\s+|ifnotexists", "", sql.lower()
            ):
                raise SchemaError(f"Unsupported index: {name}")
        if component in ledger and not current:
            raise SchemaError(f"{component} schema differs from recorded version")
        if component not in ledger:
            pending.append(component)
    if "store" in components and _sql(db, "summaries"):
        cols = set(_columns(db, "summaries"))
        cursor_columns = {"scope", "realm", "user_id", "through_id"}
        unused_columns = {"name", "text", "exportable"}
        if not cursor_columns <= cols or cols - cursor_columns - unused_columns:
            raise SchemaError("Unsupported legacy summaries")
        if "store" not in pending:
            pending.append("store")
    if "store" in components and _sql(db, "notes"):
        marker = db.execute("SELECT text FROM notes WHERE scope=?", (_MIGRATION_MARKER,)).fetchone()
        if (marker is None or marker[0] != "1") and "store" not in pending:
            pending.append("store")
    if (
        "settings" in components
        and _sql(db, "runtime_config")
        and db.execute("SELECT 1 FROM runtime_config WHERE key='retrieval_v2_mode'").fetchone()
        and "settings" not in pending
    ):
        pending.append("settings")
    return tuple(c for c in COMPONENTS if c in pending)


def _rebuild_legacy(db):
    expected, indexes = _expected()
    for table in LEGACY_REBUILD:
        spec = expected["store"][table]
        if _matches(db, table, spec):
            continue
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
        old = table + "_before_schema_upgrade"
        if _sql(db, old):
            raise SchemaError(f"Unexpected temporary table: {old}")
        db.execute(f'ALTER TABLE "{table}" RENAME TO "{old}"')
        db.execute(spec[3])
        columns = ",".join('"' + name + '"' for name in spec[0])
        db.execute(f'INSERT INTO "{table}" ({columns}) SELECT {columns} FROM "{old}"')
        db.execute(f'DROP TABLE "{old}"')
        if sequence:
            db.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
            db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", (table, sequence[0]))
        for owner, statement in indexes.values():
            if owner == table:
                # SQL comes from our isolated canonical schema, never the input DB.
                db.execute(statement)


def prepare_connection(db, components=COMPONENTS, *, expected_signature=None) -> tuple[str, ...]:
    """Prepare a new/verified-backup DB; no nested transaction or inner commit."""
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
        if "store" in pending:
            initialize_store_schema(db)
            _rebuild_legacy(db)
            marker = db.execute(
                "SELECT text FROM notes WHERE scope=?", (_MIGRATION_MARKER,)
            ).fetchone()
            if not marker or marker[0] != "1":
                logs = dict(db.execute("SELECT scope,mode FROM chat_log_modes"))
                captures = {
                    scope[len(CHATLOG_CAPTURE_NOTE_PREFIX) :]: str(text).strip()
                    for scope, text in db.execute(
                        "SELECT scope,text FROM notes WHERE scope LIKE ?",
                        (CHATLOG_CAPTURE_NOTE_PREFIX + "%",),
                    )
                    if str(text).strip() in {"all", "direct"}
                }
                replace_legacy_chatlog_settings(
                    db, legacy_chatlog_plan(logs, captures), _MIGRATION_MARKER
                )
        if "settings" in pending:
            for statement in SETTINGS_DDL:
                db.execute(statement)
            migrate_legacy_rag_override(db, {k: json.dumps(v) for k, v in LEGACY_RAG_MODES.items()})
        if "admin" in pending:
            for statement in ADMIN_DDL:
                db.execute(statement)
        if not _sql(db, "schema_migrations"):
            db.execute(LEDGER_DDL)
        for component in pending:
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(component,version,name) VALUES (?,?,?)",
                (component, 1, component + "-baseline-v1"),
            )
        if inspect_connection(db, components):
            raise SchemaError("Schema verification failed after preparation")
        db.commit()
        return pending
    except BaseException:
        db.rollback()
        raise


def require_current(db, components=COMPONENTS):
    if inspect_connection(db, components):
        raise SchemaError("Database preparation required; run hina-prepare-db --dry-run first")


def database_signature(db) -> str:
    """Content-free fingerprint of schema, all rows and sequences for offline checks."""
    digest = hashlib.sha256()
    for line in db.iterdump():
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
