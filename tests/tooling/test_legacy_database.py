"""Supported old fixtures are converted only by the explicit offline tool."""
import sqlite3
from contextlib import closing

import pytest

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.db_connections import open_existing, open_readonly
from hina_bot.core.db_schema import SchemaError
from hina_bot.core.store import Store
from hina_bot.tooling import legacy_database as legacy
from hina_bot.tooling import prepare_database as current_preparation
from hina_bot.tooling.db_migrations import inspect_connection
from hina_bot.tooling.legacy_database import prepare_connection


def test_admin_only_conversion_does_not_require_sqlite_sequence(tmp_path):
    path = tmp_path / "admin-only.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE instructions (id TEXT PRIMARY KEY,text TEXT NOT NULL,"
                   "enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),"
                   "created_at TEXT,updated_at TEXT)")
        db.execute("INSERT INTO instructions VALUES ('rule','keep',1,NULL,NULL)")
        db.commit()
    _, backup = legacy.convert_database(path, apply=True, offline_confirmed=True, components=('admin',))
    assert backup is not None
    owner = AdminDatabase.open_existing(path)
    try:
        assert owner.db.execute("SELECT text FROM instructions").fetchone()[0] == "keep"
        assert owner.db.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone() is None
    finally:
        owner.close()


def test_empty_legacy_table_keeps_absent_sequence_entry(tmp_path):
    path = tmp_path / "empty-turns.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
            realm TEXT NOT NULL,user_id TEXT NOT NULL,message_id TEXT NOT NULL UNIQUE,
            content TEXT NOT NULL,reply TEXT NOT NULL,exportable INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        assert db.execute("SELECT * FROM sqlite_sequence").fetchall() == []
    legacy.convert_database(path, apply=True, offline_confirmed=True)
    with closing(open_readonly(path)) as db:
        assert db.execute("SELECT * FROM sqlite_sequence").fetchall() == []


def test_legacy_cli_dry_run_apply_and_reexecution(tmp_path):
    path = tmp_path / "cli.sqlite3"
    legacy_database(path)
    before = signature(path)
    assert legacy.main(["--database", str(path), "--dry-run"]) == 0
    assert signature(path) == before
    assert legacy.main(["--database", str(path), "--yes"]) == 2
    assert signature(path) == before
    assert legacy.main(["--database", str(path), "--yes", "--offline-confirmed"]) == 0
    after = signature(path)
    assert legacy.main(["--database", str(path), "--yes", "--offline-confirmed"]) == 0
    assert signature(path) == after


def test_conversion_keeps_an_already_registered_admin_area(tmp_path):
    path = tmp_path / "partial-registration.sqlite3"
    legacy_database(path)
    current_preparation.prepare_database(path, components=('admin',), apply=True,
                                         offline_confirmed=True)
    admin = AdminDatabase.open_existing(path)
    with admin.db:
        admin.db.execute("INSERT INTO instructions VALUES ('rule','preserve',1,NULL,NULL)")
    admin.close()
    assert legacy.convert_database(path)[0] == ('store', 'settings')
    legacy.convert_database(path, apply=True, offline_confirmed=True)
    with closing(open_readonly(path)) as db:
        assert db.execute("SELECT text FROM instructions").fetchone()[0] == 'preserve'
        assert db.execute("SELECT version FROM schema_migrations WHERE component='admin'").fetchone()[0] == 1
        assert inspect_connection(db) == ()
from hina_bot.tooling.maintenance_backup import database_signature


def legacy_database(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                content TEXT NOT NULL, kind TEXT NOT NULL,
                origin_realm TEXT NOT NULL, origin_channel_id TEXT NOT NULL,
                origin_public_at_capture INTEGER NOT NULL, disclosure TEXT NOT NULL,
                source_message_ids TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO memory_items(id,user_id,content,kind,origin_realm,origin_channel_id,
                origin_public_at_capture,disclosure,source_message_ids,confidence)
            VALUES (5,'100','기존 기억','fact','dm:100','10',0,'local','["11","12"]',0.8);
            INSERT INTO memory_items(id,user_id,content,kind,origin_realm,origin_channel_id,
                origin_public_at_capture,disclosure)
            VALUES (20,'100','deleted','fact','dm:100','10',0,'local');
            DELETE FROM memory_items WHERE id=20;
            CREATE TABLE summaries (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
                through_id INTEGER NOT NULL, exportable INTEGER NOT NULL
            );
            INSERT INTO summaries VALUES ('dm:100:channel:10:user:100','dm:100','100',
                                         '이름','old summary',55,0);
        """)



def signature(path):
    with closing(open_readonly(path)) as db:
        return database_signature(db)



@pytest.mark.parametrize("optional_column", ["", "name TEXT", "text TEXT", "exportable INTEGER"])
def test_legacy_summary_upgrade_does_not_require_unused_fields(tmp_path, optional_column):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        extra = ", " + optional_column if optional_column else ""
        db.execute(
            "CREATE TABLE summaries (scope TEXT PRIMARY KEY, realm TEXT NOT NULL, "
            "user_id TEXT NOT NULL, through_id INTEGER NOT NULL" + extra + ")"
        )
        db.execute(
            "INSERT INTO summaries(scope,realm,user_id,through_id) VALUES (?,?,?,?)",
            ("dm:100:channel:10:user:100", "dm:100", "100", 55),
        )
        db.commit()
    before = signature(path)
    assert legacy.convert_database(path)[0] == ("store", "settings", "admin")
    assert signature(path) == before
    _, backup = legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(backup.path) == before
    with closing(open_readonly(path)) as db:
        assert inspect_connection(db) == ()
        assert tuple(db.execute("SELECT scope,realm,user_id,through_id "
                                "FROM memory_extraction_cursors").fetchone()) == (
            "dm:100:channel:10:user:100", "dm:100", "100", 55,
        )
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='summaries'").fetchone()
    assert legacy.convert_database(path, apply=True, offline_confirmed=True) == ((), None)



@pytest.mark.parametrize("columns", [
    "scope TEXT, realm TEXT, user_id TEXT",  # A cursor cannot be recovered without through_id.
    "scope TEXT, realm TEXT, user_id TEXT, through_id INTEGER, unknown TEXT",
])
def test_unrecognized_legacy_summary_columns_are_rejected_without_writes(tmp_path, columns):
    path = tmp_path / "unsupported.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE summaries (" + columns + ")")
    before = signature(path)
    with pytest.raises(SchemaError, match="Unsupported legacy summaries"):
        legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    assert not (tmp_path / "backups").exists()



def test_legacy_backup_upgrade_and_actual_restore_preserve_sources_and_sequence(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    before = signature(path)
    assert legacy.convert_database(path)[0] == ("store", "settings", "admin")
    assert signature(path) == before and not (tmp_path / "backups").exists()
    _, backup = legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(backup.path) == before
    with closing(open_readonly(path)) as db:
        assert inspect_connection(db) == ()
        assert tuple(
            db.execute(
                "SELECT id,content,source_message_ids,confidence FROM memory_items"
            ).fetchone()
        ) == (5, "기존 기억", '["11","12"]', 0.8)
        assert (
            db.execute("SELECT seq FROM sqlite_sequence WHERE name='memory_items'").fetchone()[0]
            == 20
        )
        assert db.execute("SELECT through_id FROM memory_extraction_cursors").fetchone()[0] == 55
        assert db.execute("SELECT COUNT(*) FROM runtime_config_startup").fetchone()[0] == 0
    # Restore the real SQLite snapshot to a different file, not just hash-check it.
    restored = tmp_path / "restored.sqlite3"
    with (
        closing(open_readonly(backup.path)) as source,
        closing(sqlite3.connect(restored)) as target,
    ):
        source.backup(target)
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert signature(restored) == before
    legacy.convert_database(restored, apply=True, offline_confirmed=True)
    with closing(open_readonly(restored)) as db:
        assert (
            db.execute("SELECT content FROM memory_items WHERE id=5").fetchone()[0] == "기존 기억"
        )
        assert (
            db.execute("SELECT seq FROM sqlite_sequence WHERE name='memory_items'").fetchone()[0]
            == 20
        )



@pytest.mark.parametrize("mutation", ["column", "check", "dependency", "future", "drift"])
def test_unsupported_schema_is_rejected_without_stamp_or_backup(tmp_path, mutation):
    path = tmp_path / "unknown.sqlite3"
    if mutation in {"future", "drift"}:
        current_preparation.prepare_database(path, apply=True, offline_confirmed=True)
    else:
        legacy_database(path)
    with closing(open_existing(path)) as db, db:
        if mutation == "column":
            db.execute("ALTER TABLE memory_items ADD COLUMN unsupported TEXT")
        elif mutation == "check":
            db.execute(
                "ALTER TABLE memory_items ADD COLUMN revision INTEGER NOT NULL DEFAULT 0 "
                "CHECK(revision<99)"
            )
        elif mutation == "dependency":
            db.execute("CREATE INDEX custom_memory_idx ON memory_items(content)")
        elif mutation == "future":
            db.execute("UPDATE schema_migrations SET version=99")
        else:
            db.execute("DROP INDEX turns_owner")
    before = signature(path)
    with pytest.raises(SchemaError):
        legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    assert not (tmp_path / "backups").exists()



@pytest.mark.parametrize("fail_at", ["copy", "index", "ledger"])
def test_migration_failure_rolls_back_ddl_rows_and_ledger(tmp_path, monkeypatch, fail_at):
    from hina_bot.tooling import legacy_database as db_schema

    path = tmp_path / "rollback.sqlite3"
    legacy_database(path)
    before = signature(path)
    db = open_existing(path)
    try:
        if fail_at == "copy":
            original = db_schema._rebuild_legacy

            def abort_after_copy(connection, components):
                original(connection, components)
                # Another connection still sees the original tables and sources.
                assert signature(path) == before
                raise RuntimeError("interrupted after copy")

            monkeypatch.setattr(db_schema, "_rebuild_legacy", abort_after_copy)
        else:

            def reject_write(action, name, *rest):
                rejected = (
                    action == sqlite3.SQLITE_INSERT and name == "schema_migrations"
                    if fail_at == "ledger"
                    else action == sqlite3.SQLITE_CREATE_INDEX and name == "memory_items_owner_status"
                )
                return sqlite3.SQLITE_DENY if rejected else sqlite3.SQLITE_OK

            db.set_authorizer(reject_write)
        with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
            prepare_connection(db)
        db.set_authorizer(None)
        assert not db.in_transaction
    finally:
        db.close()
    assert signature(path) == before
    monkeypatch.undo()
    legacy.convert_database(path, apply=True, offline_confirmed=True)
    with closing(open_readonly(path)) as db:
        assert inspect_connection(db) == ()



def test_post_backup_source_change_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "changed.sqlite3"
    legacy_database(path)
    original = legacy.backup_schema_database

    def backup_then_change(*args, **kwargs):
        result = original(*args, **kwargs)
        with closing(open_existing(path)) as db, db:
            db.execute("UPDATE memory_items SET content='changed' WHERE id=5")
        return result

    monkeypatch.setattr(legacy, "backup_schema_database", backup_then_change)
    with pytest.raises(SchemaError, match="changed since backup"):
        legacy.convert_database(path, apply=True, offline_confirmed=True)
    with closing(open_readonly(path)) as db:
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
            is None
        )
        assert db.execute("SELECT content FROM memory_items").fetchone()[0] == "changed"



@pytest.mark.parametrize(
    "definition",
    [
        "scope TEXT PRIMARY KEY COLLATE NOCASE, text TEXT NOT NULL",
        "scope TEXT PRIMARY KEY ON CONFLICT REPLACE, text TEXT NOT NULL",
    ],
)
def test_unversioned_unknown_key_semantics_are_not_stamped(tmp_path, definition):
    path = tmp_path / "constraints.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE notes (" + definition + ")")
    before = signature(path)
    with pytest.raises(SchemaError, match="Unsupported table semantics"):
        legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before



def test_unknown_unique_index_collation_is_rejected(tmp_path):
    path = tmp_path / "index-collation.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE notes(scope TEXT PRIMARY KEY, text TEXT NOT NULL)")
        db.execute("CREATE UNIQUE INDEX extra_key ON notes(scope COLLATE NOCASE)")
    before = signature(path)
    with pytest.raises(SchemaError, match="Unsupported constraints"):
        legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before



def test_explicit_chatlog_conversion_keeps_existing_whitespace_handling(tmp_path):
    from hina_bot.core.scope_overrides import CHATLOG_CAPTURE_NOTE_PREFIX

    path = tmp_path / "capture.sqlite3"
    store = Store.create(str(path))
    store.set_note("config:chatlog_unified_v1", "")
    store.db.execute("DROP TABLE schema_migrations")
    store.set_chat_log_mode_override("global", "on")
    store.set_note(CHATLOG_CAPTURE_NOTE_PREFIX + "global", " direct ")
    store.set_note("manual", "kept")
    store.close()
    legacy.convert_database(path, components=("store", "settings"), apply=True, offline_confirmed=True)
    store = Store.open_existing(str(path))
    try:
        assert store.note(CHATLOG_CAPTURE_NOTE_PREFIX + "global") == "direct"
        assert store.note("manual") == "kept"
    finally:
        store.close()



def test_check_literal_case_is_part_of_the_schema_contract(tmp_path):
    path = tmp_path / "check-case.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE memory_modes (
            scope TEXT PRIMARY KEY, mode TEXT NOT NULL
            CHECK(mode IN ('NORMAL','READ_ONLY','WRITE_ONLY','OFF')))""")
    before = signature(path)
    with pytest.raises(SchemaError, match="Unsupported constraints"):
        legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before



@pytest.mark.parametrize("global_mode", ["on", "off"])
def test_legacy_chatlog_inheritance_and_invalid_capture_cleanup_are_preserved(tmp_path, global_mode):
    prefix = "config:chatlog_capture:"
    path = tmp_path / "legacy-chatlog.sqlite3"
    store = Store.create(path)
    try:
        store.set_note("config:chatlog_unified_v1", "")
        store.db.execute("DROP TABLE schema_migrations")
        for scope, mode in {"global": global_mode, "guild:1": "on",
                            "guild:1:channel:10": "off"}.items():
            store.set_chat_log_mode_override(scope, mode)
        for scope, capture in {"global": " direct ", "guild:1": "all",
                               "guild:1:channel:10": "direct",
                               "guild:2:channel:20": "direct", "guild:3": "invalid"}.items():
            store.set_note(prefix + scope, capture)
        store.set_note("manual", "preserved")
    finally:
        store.close()
    before = signature(path)
    _, backup = legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(backup.path) == before
    with closing(open_readonly(path)) as db:
        assert dict(db.execute("SELECT scope,mode FROM chat_log_modes")) == {
            "global": global_mode, "guild:1": "on", "guild:1:channel:10": "off",
            "guild:2:channel:20": global_mode,
        }
        notes = dict(db.execute("SELECT scope,text FROM notes"))
        assert notes == {
            "manual": "preserved", "config:chatlog_unified_v1": "1",
            prefix + "global": "all" if global_mode == "off" else "direct",
            prefix + "guild:1": "all", prefix + "guild:1:channel:10": "all",
            prefix + "guild:2:channel:20": "all" if global_mode == "off" else "direct",
        }
    after = signature(path)
    assert legacy.convert_database(path, apply=True, offline_confirmed=True) == ((), None)
    assert signature(path) == after



def test_legacy_rebuild_keeps_edit_history_proposals_and_their_id_high_marks(tmp_path):
    path = tmp_path / "legacy-relations.sqlite3"
    legacy_database(path)
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            ALTER TABLE memory_items ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
            UPDATE memory_items SET revision=4 WHERE id=5;
            CREATE TABLE memory_item_edit_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, memory_item_id INTEGER NOT NULL,
                revision INTEGER NOT NULL, content TEXT NOT NULL, kind TEXT NOT NULL,
                disclosure TEXT NOT NULL, confidence REAL NOT NULL,
                relationship_evidence TEXT NOT NULL DEFAULT '{}',
                edited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, admin_command_id INTEGER
            );
            INSERT INTO memory_item_edit_history(id,memory_item_id,revision,content,kind,
                disclosure,confidence,relationship_evidence,admin_command_id)
            VALUES (8,5,3,'earlier text','fact','local',0.9,'{"support":0.6}',41),
                   (20,5,4,'deleted','fact','local',1,'{}',NULL);
            DELETE FROM memory_item_edit_history WHERE id=20;
            CREATE TABLE memory_reconciliation_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                origin_realm TEXT NOT NULL, origin_channel_id TEXT NOT NULL,
                new_memory_item_id INTEGER NOT NULL, target_memory_item_id INTEGER NOT NULL,
                relation TEXT NOT NULL, confidence REAL NOT NULL,
                source_message_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO memory_reconciliation_proposals(id,user_id,origin_realm,origin_channel_id,
                new_memory_item_id,target_memory_item_id,relation,confidence,source_message_ids)
            VALUES (9,'100','dm:100','10',5,6,'corrects',0.99,'["11","12"]'),
                   (30,'100','dm:100','10',5,7,'conflicts',0.8,'[]');
            DELETE FROM memory_reconciliation_proposals WHERE id=30;
        """)
        history = db.execute("SELECT * FROM memory_item_edit_history").fetchall()
        proposals = db.execute("SELECT * FROM memory_reconciliation_proposals").fetchall()
    before = signature(path)
    _, backup = legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(backup.path) == before
    restored = tmp_path / "restored-relations.sqlite3"
    with closing(open_readonly(backup.path)) as source, closing(sqlite3.connect(restored)) as target:
        source.backup(target)
    assert signature(restored) == before
    legacy.convert_database(restored, apply=True, offline_confirmed=True)
    for candidate in (path, restored):
        with closing(open_readonly(candidate)) as db:
            assert [tuple(row) for row in db.execute("SELECT * FROM memory_item_edit_history")] == history
            assert [tuple(row) for row in db.execute("SELECT * FROM memory_reconciliation_proposals")] == proposals
            assert db.execute("SELECT revision FROM memory_items WHERE id=5").fetchone()[0] == 4
            sequences = dict(db.execute("SELECT name,seq FROM sqlite_sequence"))
            assert sequences["memory_items"] == sequences["memory_item_edit_history"] == 20
            assert sequences["memory_reconciliation_proposals"] == 30
            assert inspect_connection(db) == ()  # Includes every restored index/constraint.



@pytest.mark.parametrize("foreign_keys", [False, True])
def test_external_foreign_key_refuses_rebuild_without_breaking_or_deleting_children(
    tmp_path, foreign_keys,
):
    path = tmp_path / "external-fk.sqlite3"
    legacy_database(path)
    with closing(open_existing(path)) as db:
        with db:
            db.execute("""CREATE TABLE external_links (
                item_id INTEGER REFERENCES MEMORY_ITEMS(id) ON DELETE CASCADE)""")
            db.execute("INSERT INTO external_links VALUES (5)")
        db.execute("PRAGMA foreign_keys=" + str(int(foreign_keys)))
        before = signature(path)
        with pytest.raises(SchemaError, match="Unsupported rebuild foreign key: external_links"):
            prepare_connection(db, ("store",))
        assert not db.in_transaction
        assert signature(path) == before
        assert db.execute("SELECT item_id FROM external_links").fetchone()[0] == 5
        assert db.execute("PRAGMA foreign_key_list(external_links)").fetchone()[2] == "MEMORY_ITEMS"
    with pytest.raises(SchemaError, match="Unsupported rebuild foreign key: external_links"):
        legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    assert not (tmp_path / "backups").exists()



@pytest.mark.parametrize("custom_sql", [
    "CREATE VIEW external_memory AS SELECT id FROM memory_items",
    """CREATE TABLE external_events(id INTEGER);
       CREATE TRIGGER external_memory_trigger AFTER INSERT ON external_events
       BEGIN UPDATE memory_items SET content='changed' WHERE id=NEW.id; END""",
    # No SQL dependency parser: even an unrelated custom view requires manual review.
    "CREATE VIEW unrelated_view AS SELECT 1 AS id",
])
def test_custom_views_and_triggers_refuse_rebuild_before_rename(tmp_path, custom_sql):
    path = tmp_path / "custom-objects.sqlite3"
    legacy_database(path)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(custom_sql)
    before = signature(path)
    with pytest.raises(SchemaError, match="Unsupported rebuild dependency"):
        legacy.convert_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    assert not (tmp_path / "backups").exists()
