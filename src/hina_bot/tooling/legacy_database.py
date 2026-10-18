"""Offline-only conversion of supported unversioned Hina databases to v1.

This module is never imported by Store, AdminDatabase or normal preparation.
Legacy structure analysis stays here; runtime accepts only fixed current shapes.
"""

import argparse
import json
import re
import sqlite3
from functools import cache
from pathlib import Path

from hina_bot.core.config import LEGACY_RAG_MODES
from hina_bot.core.db_connections import open_existing, open_readonly
from hina_bot.core.db_ddl import DDL, LEDGER_DDL, QUEUE_DDL, STORE_DDL, V1_TABLES
from hina_bot.core.db_schema import SchemaError, require_current, schema_versions
from hina_bot.core.scope_overrides import CHATLOG_CAPTURE_NOTE_PREFIX

from .maintenance_backup import backup_schema_database, database_signature

COMPONENTS = tuple(DDL)
# Only these known legacy columns may be absent. Inspection and ALTER use the
# same list; declarations are the existing upgrades, not a general schema DSL.
LEGACY_COLUMNS = {
    "turns": {
        "memory_context": "TEXT NOT NULL DEFAULT ''",
        "context_provenance": "TEXT NOT NULL DEFAULT ''",
        "turn_id": "TEXT",
        "name": "TEXT NOT NULL DEFAULT ''",
    },
    "memory_items": {
        "relationship_evidence": "TEXT NOT NULL DEFAULT '{}'",
        "user_name": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT 'active' "
                  "CHECK(status IN ('active','superseded','retracted'))",
        "superseded_by": "INTEGER",
        "revision": "INTEGER NOT NULL DEFAULT 0",
    },
}
# These schemas existed before CHECK constraints were installed. Copy all columns,
# preserving IDs and sequences, rather than blessing a weaker schema as current.
LEGACY_REBUILD = {"memory_items", "memory_item_edit_history", "memory_reconciliation_proposals"}
CHATLOG_MIGRATION_MARKER = "config:chatlog_unified_v1"





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
    """Ask SQLite for our fixed DDL's columns/checks/keys/SQL and named indexes."""
    db = sqlite3.connect(":memory:")
    try:
        result = {}
        for statements in DDL.values():
            for statement in statements:
                db.execute(statement)
        for component, tables in V1_TABLES.items():
            result[component] = {
                table: (_columns(db, table), _checks(_sql(db, table)),
                        _unique_keys(db, table), _sql(db, table))
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
    versions = schema_versions(db)
    registered = tuple(component for component in components if component in versions)
    require_current(db, registered)
    components = tuple(component for component in components if component not in versions)
    if not components:
        return ()
    for component in COMPONENTS:
        if component not in components:
            continue
        for table, spec in expected[component].items():
            expected_columns, expected_checks, expected_keys, expected_sql = spec
            sql = _sql(db, table)
            if sql is None:
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
            missing = expected_columns.keys() - cols.keys()
            if cols.keys() - expected_columns.keys() or missing - LEGACY_COLUMNS.get(table, {}).keys():
                raise SchemaError(f"Unsupported columns in {table}")
            for name, column in cols.items():
                # Column position is deliberately excluded: ALTER appends columns.
                if column != expected_columns[name]:
                    raise SchemaError(f"Unsupported column definition: {table}.{name}")
            checks = _checks(sql)
            allowed = expected_checks
            if table == "memory_items":
                allowed |= {"statusin('active','superseded')"}
            keys = _unique_keys(db, table)
            if checks - allowed or keys - expected_keys:
                raise SchemaError(f"Unsupported constraints in {table}")
            if db.execute(f'PRAGMA foreign_key_list("{table}")').fetchone():
                raise SchemaError(f"Unsupported foreign keys in {table}")
            if "autoincrement" in expected_sql.lower() and "autoincrement" not in sql.lower():
                raise SchemaError(f"Missing AUTOINCREMENT in {table}")
            exact = cols == expected_columns and checks == expected_checks and keys == expected_keys
            if not exact and table not in LEGACY_REBUILD and not missing:
                raise SchemaError(f"Unsupported constraints in {table}")
        for name, (table, sql) in indexes.items():
            if table not in expected[component]:
                continue
            found = _sql(db, name)
            if found is not None and re.sub(r"\s+", "", found.lower()).replace("ifnotexists", "") != re.sub(
                r"\s+|ifnotexists", "", sql.lower()
            ):
                raise SchemaError(f"Unsupported index: {name}")
    if "store" in components and _sql(db, "summaries"):
        cols = set(_columns(db, "summaries"))
        cursor_columns = {"scope", "realm", "user_id", "through_id"}
        unused_columns = {"name", "text", "exportable"}
        if not cursor_columns <= cols or cols - cursor_columns - unused_columns:
            raise SchemaError("Unsupported legacy summaries")
    rewritten = {
        table for component in components for table, spec in expected[component].items()
        if _sql(db, table) is not None and _sql(db, table) != spec[3]
    }
    if "summaries" in tables and "store" in components:
        rewritten.add("summaries")
    if rewritten:
        for kind, name, owner in db.execute(
            "SELECT type,name,tbl_name FROM sqlite_master WHERE sql IS NOT NULL "
            "AND type IN ('view','trigger','index')"
        ):
            if kind in {'view', 'trigger'} or (owner in rewritten and name not in indexes):
                raise SchemaError(f"Unsupported rebuild dependency: {name}")
        for dependent in tables:
            for row in db.execute('SELECT "table" FROM pragma_foreign_key_list(?)', (dependent,)):
                if row[0].lower() in rewritten:
                    raise SchemaError(f"Unsupported rebuild foreign key: {dependent}")
    return tuple(c for c in COMPONENTS if c in components)


def _rebuild_legacy(db, components=COMPONENTS):
    expected, indexes = _expected()
    for component in components:
        for table, spec in expected[component].items():
            if _sql(db, table) == spec[3]:
                continue
            row = (
                db.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
                if "AUTOINCREMENT" in spec[3].upper() else None
            )
            old = table + "_before_schema_upgrade"
            if _sql(db, old):
                raise SchemaError(f"Unexpected temporary table: {old}")
            db.execute(f'ALTER TABLE "{table}" RENAME TO "{old}"')
            db.execute(spec[3])
            columns = ",".join('"' + name + '"' for name in spec[0])
            db.execute(f'INSERT INTO "{table}" ({columns}) SELECT {columns} FROM "{old}"')
            db.execute(f'DROP TABLE "{old}"')
            if "AUTOINCREMENT" in spec[3].upper():
                db.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
                if row:
                    db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", (table, row[0]))
    # Recreate known indexes in their canonical spelling after all rebuilds.
    for name, (table, sql) in indexes.items():
        if any(table in expected[component] for component in components):
            db.execute('DROP INDEX IF EXISTS "' + name + '"')
            db.execute(sql)


def _migrate_legacy_chatlog(db):
    marker = db.execute("SELECT text FROM notes WHERE scope=?",
                        (CHATLOG_MIGRATION_MARKER,)).fetchone()
    if marker and marker[0] == "1":
        return
    logs = dict(db.execute("SELECT scope,mode FROM chat_log_modes"))
    captures = {
        scope[len(CHATLOG_CAPTURE_NOTE_PREFIX):]: str(text).strip()
        for scope, text in db.execute(
            "SELECT scope,text FROM notes WHERE scope LIKE ?", (CHATLOG_CAPTURE_NOTE_PREFIX + "%",)
        )
        if str(text).strip() in {"all", "direct"}
    }
    # Read both old representations before replacing either. All writes and the
    # completion marker belong to prepare_connection's one transaction.
    db.execute("DELETE FROM chat_log_modes")
    db.execute("DELETE FROM notes WHERE scope LIKE ?", (CHATLOG_CAPTURE_NOTE_PREFIX + "%",))
    for key in set(logs) | set(captures):
        if key == "global":
            parents = ("global",)
        elif key.startswith("guild:") and ":channel:" in key:
            parents = ("global", key.split(":channel:", 1)[0], key)
        else:
            parents = ("global", key)
        enabled, capture = "on", "all"
        for parent in parents:
            enabled = logs.get(parent, enabled)
            capture = captures.get(parent, capture)
        db.execute("INSERT INTO chat_log_modes(scope,mode) VALUES (?,?)",
                   (key, "off" if enabled == "off" else "on"))
        db.execute("INSERT INTO notes(scope,text) VALUES (?,?)",
                   (CHATLOG_CAPTURE_NOTE_PREFIX + key, "all" if enabled == "off" else capture))
    db.execute("INSERT OR REPLACE INTO notes(scope,text) VALUES (?,?)",
               (CHATLOG_MIGRATION_MARKER, "1"))


def _prepare_store(db):
    for statement in STORE_DDL + QUEUE_DDL:
        db.execute(statement)
    for table, declarations in LEGACY_COLUMNS.items():
        columns = _columns(db, table)
        for name, declaration in declarations.items():
            if name not in columns:
                db.execute(f'ALTER TABLE "{table}" ADD COLUMN {name} {declaration}')
    if _sql(db, "summaries"):
        # Existing structured cursors win. The original summary remains in the
        # verified pre-migration backup, as in the existing conversion contract.
        db.execute("""INSERT OR IGNORE INTO memory_extraction_cursors(scope,realm,user_id,through_id)
                      SELECT scope,realm,user_id,through_id FROM summaries""")
        db.execute("DROP TABLE summaries")
    _migrate_legacy_chatlog(db)


def _migrate_legacy_rag(db):
    row = db.execute("SELECT value FROM runtime_config WHERE key='retrieval_v2_mode'").fetchone()
    if row is None:
        return
    if not db.execute("SELECT 1 FROM runtime_config WHERE key='rag_mode'").fetchone():
        try:
            legacy = json.loads(row[0])
        except (TypeError, ValueError):
            legacy = None
        if isinstance(legacy, str) and legacy in LEGACY_RAG_MODES:
            db.execute("INSERT INTO runtime_config(key,value) VALUES (?,?)",
                       ("rag_mode", json.dumps(LEGACY_RAG_MODES[legacy])))
    db.execute("DELETE FROM runtime_config WHERE key='retrieval_v2_mode'")


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
            _prepare_store(db)
        if "settings" in pending:
            for statement in DDL["settings"]:
                db.execute(statement)
            _migrate_legacy_rag(db)
        if "admin" in pending:
            for statement in DDL["admin"]:
                db.execute(statement)
        _rebuild_legacy(db, pending)
        if not _sql(db, "schema_migrations"):
            db.execute(LEDGER_DDL)
        for component in pending:
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(component,version,name) VALUES (?,?,?)",
                (component, 1, component + "-baseline-v1"),
            )
        require_current(db, components)
        db.commit()
        return pending
    except BaseException:
        db.rollback()
        raise


def convert_database(path, *, apply=False, components=COMPONENTS, backup_dir=None,
                     offline_confirmed=False):
    """Inspect, back up and convert one explicitly chosen existing offline file."""
    path = Path(path).expanduser()
    db = open_readonly(path)
    try:
        db.execute("BEGIN")
        pending = inspect_connection(db, components)
        expected = database_signature(db) if apply and pending else None
    finally:
        db.close()
    if not apply or not pending:
        return pending, None
    if not offline_confirmed:
        raise ValueError("Stop all database writers and pass --offline-confirmed")
    backup = backup_schema_database(path, backup_dir=backup_dir)
    db = open_existing(path)
    try:
        changed = prepare_connection(db, components, expected_signature=expected)
        return changed, backup
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--backup-dir")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--yes", action="store_true")
    parser.add_argument("--offline-confirmed", action="store_true")
    args = parser.parse_args(argv)
    try:
        pending, backup = convert_database(
            args.database, apply=args.yes, backup_dir=args.backup_dir,
            offline_confirmed=args.offline_confirmed,
        )
        print("Schema areas: " + (", ".join(pending) or "already current"))
        if backup:
            print(f"Backup: {backup.path}; SHA256={backup.sha256}")
        if not args.yes:
            print("Dry-run only; no database changes.")
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 2



if __name__ == "__main__":
    raise SystemExit(main())
