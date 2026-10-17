"""File-level contracts for explicit preparation, inspection and recovery."""

import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.llm import LLM
from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.config import Settings
from hina_bot.core.db_connections import open_existing, open_readonly
from hina_bot.core.db_schema import (
    SchemaError,
    database_signature,
    inspect_connection,
    prepare_connection,
)
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.tooling import prepare_database as preparation
from hina_bot.tooling.runtime_migration import migrate


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
    assert preparation.prepare_database(path)[0] == ("store", "settings", "admin")
    assert signature(path) == before
    _, backup = preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(backup.path) == before
    with closing(open_readonly(path)) as db:
        assert inspect_connection(db) == ()
        assert tuple(db.execute("SELECT scope,realm,user_id,through_id "
                                "FROM memory_extraction_cursors").fetchone()) == (
            "dm:100:channel:10:user:100", "dm:100", "100", 55,
        )
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='summaries'").fetchone()
    assert preparation.prepare_database(path, apply=True, offline_confirmed=True) == ((), None)


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
        preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    assert not (tmp_path / "backups").exists()


def test_missing_inspection_never_creates_file_or_parent(tmp_path):
    path = tmp_path / "absent" / "한글 ?#.sqlite3"
    assert preparation.main(["--database", str(path), "--dry-run"]) == 0
    with pytest.raises(sqlite3.OperationalError):
        open_readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        Store.open_existing(str(path))
    assert not path.parent.exists()
    assert preparation.main(["--database", str(path), "--yes"]) == 2
    assert not path.parent.exists()


def test_bootstrap_repeated_prepare_and_open_do_not_rewrite_snapshot(tmp_path):
    path = tmp_path / "한글 ?#.sqlite3"
    changed, backup = preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert changed == ("store", "settings", "admin") and backup is None
    store = Store.open_existing(str(path))
    RuntimeSettings(Settings(discord_token="test", model="base"), store).capture_startup_snapshot()
    before = signature(path)
    trace = []
    store.db.set_trace_callback(trace.append)
    # Constructors bind existing connections only, and settings only load overrides.
    facade = Store(store.db)
    RuntimeSettings(Settings(discord_token="test", model="other"), facade)
    assert not any(
        s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"))
        for s in trace
    )
    store.close()
    assert preparation.prepare_database(path, apply=True, offline_confirmed=True) == ((), None)
    assert signature(path) == before
    assert not (tmp_path / "backups").exists()


def test_legacy_backup_upgrade_and_actual_restore_preserve_sources_and_sequence(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    before = signature(path)
    assert preparation.prepare_database(path)[0] == ("store", "settings", "admin")
    assert signature(path) == before and not (tmp_path / "backups").exists()
    _, backup = preparation.prepare_database(path, apply=True, offline_confirmed=True)
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
    preparation.prepare_database(restored, apply=True, offline_confirmed=True)
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
        preparation.prepare_database(path, apply=True, offline_confirmed=True)
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
        preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("fail_at", ["copy", "index", "ledger"])
def test_migration_failure_rolls_back_ddl_rows_and_ledger(tmp_path, monkeypatch, fail_at):
    from hina_bot.core import db_schema

    path = tmp_path / "rollback.sqlite3"
    legacy_database(path)
    before = signature(path)
    db = open_existing(path)
    try:
        if fail_at == "copy":
            original = db_schema._rebuild_legacy

            def abort_after_copy(connection):
                original(connection)
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
    preparation.prepare_database(path, apply=True, offline_confirmed=True)
    with closing(open_readonly(path)) as db:
        assert inspect_connection(db) == ()


def test_post_backup_source_change_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "changed.sqlite3"
    legacy_database(path)
    original = preparation.backup_schema_database

    def backup_then_change(*args, **kwargs):
        result = original(*args, **kwargs)
        with closing(open_existing(path)) as db, db:
            db.execute("UPDATE memory_items SET content='changed' WHERE id=5")
        return result

    monkeypatch.setattr(preparation, "backup_schema_database", backup_then_change)
    with pytest.raises(SchemaError, match="changed since backup"):
        preparation.prepare_database(path, apply=True, offline_confirmed=True)
    with closing(open_readonly(path)) as db:
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
            is None
        )
        assert db.execute("SELECT content FROM memory_items").fetchone()[0] == "changed"


@pytest.mark.parametrize("exists", [False, True])
def test_json_dry_run_uses_clone_and_keeps_target_untouched(tmp_path, exists):
    path = tmp_path / "target" / "admin.sqlite3"
    if exists:
        path.parent.mkdir()
        legacy_database(path)
    before = signature(path) if exists else None
    source = tmp_path / "instructions.json"
    source.write_text(json.dumps([{"id": "test.rule", "text": "import me"}]))
    args = SimpleNamespace(
        database=str(path),
        instructions=str(source),
        facts="",
        contexts="",
        dry_run=True,
        replace=False,
    )
    assert migrate(args)["instructions"] == {"added": 1}
    if exists:
        assert signature(path) == before
    else:
        assert not path.parent.exists()
    assert not (path.parent / "backups").exists()


@pytest.mark.asyncio
async def test_llm_default_does_not_open_settings_database_and_eval_copy_is_isolated(tmp_path):
    path = tmp_path / "absent" / "runtime.sqlite3"
    settings = Settings(discord_token="test", db_path=str(path), usage_log_path="")
    client = SimpleNamespace(close=AsyncMock())
    llm = LLM(settings, client=client)
    await llm.close()
    assert not path.parent.exists()
    preparation.prepare_database(path, apply=True, offline_confirmed=True)
    before = signature(path)
    admin = AdminDatabase.copy_to_memory(path)
    try:
        from hina_bot.core.instructions import InstructionRegistry

        InstructionRegistry(admin).add("eval.rule", "isolated")
    finally:
        admin.close()
    assert signature(path) == before


def test_legacy_initializing_constructors_fail_early_instead_of_touching_paths(tmp_path):
    path = tmp_path / "absent" / "runtime.sqlite3"
    for constructor in (Store, AdminDatabase):
        with pytest.raises(TypeError, match="open_existing"):
            constructor(str(path))
    assert not path.parent.exists()


def test_unrelated_database_is_not_adopted_as_hina(tmp_path):
    path = tmp_path / "unrelated.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY, content TEXT)")
    before = signature(path)
    with pytest.raises(SchemaError, match="recognized Hina"):
        preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    assert not (tmp_path / "backups").exists()


def test_unversioned_current_database_is_adopted_without_rewriting_user_state(tmp_path):
    path = tmp_path / "unversioned.sqlite3"
    preparation.prepare_database(path, apply=True, offline_confirmed=True)
    store = Store.open_existing(str(path))
    store.set_note("manual", "preserved")
    runtime = RuntimeSettings(Settings(discord_token="test", model="actual-startup"), store)
    runtime.capture_startup_snapshot()
    runtime.set_text("channel_context_chars", "4300")
    with store.db:
        store.db.execute("DROP TABLE schema_migrations")
    user_state = [line for line in store.db.iterdump()]
    store.close()
    with pytest.raises(SchemaError, match="preparation required"):
        Store.open_existing(str(path))
    _, backup = preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert backup is not None
    with closing(open_readonly(path)) as db:
        assert [line for line in db.iterdump() if "schema_migrations" not in line] == user_state
    store = Store.open_existing(str(path))
    try:
        assert store.note("manual") == "preserved"
        assert RuntimeSettings(Settings(discord_token="test"), store).channel_context_chars == 4300
    finally:
        store.close()


def test_all_maintenance_inspections_keep_the_target_connection_readonly(tmp_path, monkeypatch):
    from hina_bot.tooling import (
        flush_pending_memory,
        maintenance_db,
        migrate_conversation_reset_epochs,
        reset_analysis_data,
        reset_conversation,
    )

    path = tmp_path / "plans.sqlite3"
    preparation.prepare_database(path, apply=True, offline_confirmed=True)
    before = signature(path)
    statements = []
    opens = []

    def tracked_open(path):
        db = open_readonly(path)
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        db.set_trace_callback(statements.append)
        opens.append(path)
        return db

    monkeypatch.setattr(maintenance_db, "open_readonly", tracked_open)
    monkeypatch.setattr(flush_pending_memory, "open_readonly", tracked_open)
    assert flush_pending_memory.inspect_pending(path) == ()
    assert reset_conversation.inspect_reset(path).ready
    assert reset_analysis_data.build_reset_plan(path, usage_log_path="", event_log_path="").safe
    assert migrate_conversation_reset_epochs.main(["--database", str(path), "--dry-run"]) == 0
    assert len(opens) == 4
    assert not any(
        statement.lstrip()
        .upper()
        .startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"))
        for statement in statements
    )
    assert not any(
        any(
            word in statement.lower()
            for word in (
                "wal_checkpoint",
                "journal_mode",
                "secure_delete",
            )
        )
        for statement in statements
    )
    assert signature(path) == before


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
        preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before


def test_unknown_unique_index_collation_is_rejected(tmp_path):
    path = tmp_path / "index-collation.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE notes(scope TEXT PRIMARY KEY, text TEXT NOT NULL)")
        db.execute("CREATE UNIQUE INDEX extra_key ON notes(scope COLLATE NOCASE)")
    before = signature(path)
    with pytest.raises(SchemaError, match="Unsupported constraints"):
        preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before


def test_explicit_chatlog_conversion_keeps_existing_whitespace_handling(tmp_path):
    from hina_bot.core.scope_overrides import CHATLOG_CAPTURE_NOTE_PREFIX

    path = tmp_path / "capture.sqlite3"
    store = Store.create(str(path))
    store.set_note("config:chatlog_unified_v1", "")
    store.set_chat_log_mode_override("global", "on")
    store.set_note(CHATLOG_CAPTURE_NOTE_PREFIX + "global", " direct ")
    store.set_note("manual", "kept")
    store.close()
    preparation.prepare_database(path, components=("store",), apply=True, offline_confirmed=True)
    store = Store.open_existing(str(path))
    try:
        assert store.note(CHATLOG_CAPTURE_NOTE_PREFIX + "global") == "direct"
        assert store.note("manual") == "kept"
    finally:
        store.close()


def test_preparation_cli_uses_the_same_dotenv_database_path_as_the_bot(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    # Record environment restoration even when DATABASE_PATH was originally absent.
    monkeypatch.setenv("DATABASE_PATH", "")
    monkeypatch.delenv("DATABASE_PATH")
    path = tmp_path / "configured" / "bot.sqlite3"
    (tmp_path / ".env.local").write_text("DATABASE_PATH=" + str(path) + "\n")
    assert preparation.main(["--dry-run"]) == 0
    assert not path.parent.exists()
    assert str(path) in capsys.readouterr().out
    assert preparation.main(["--yes", "--offline-confirmed"]) == 0
    assert path.is_file()
    assert not (tmp_path / "data" / "hina.sqlite3").exists()


def test_check_literal_case_is_part_of_the_schema_contract(tmp_path):
    path = tmp_path / "check-case.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE memory_modes (
            scope TEXT PRIMARY KEY, mode TEXT NOT NULL
            CHECK(mode IN ('NORMAL','READ_ONLY','WRITE_ONLY','OFF')))""")
    before = signature(path)
    with pytest.raises(SchemaError, match="Unsupported constraints"):
        preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before


@pytest.mark.parametrize("global_mode", ["on", "off"])
def test_legacy_chatlog_inheritance_and_invalid_capture_cleanup_are_preserved(tmp_path, global_mode):
    prefix = "config:chatlog_capture:"
    path = tmp_path / "legacy-chatlog.sqlite3"
    store = Store.create(path)
    try:
        store.set_note("config:chatlog_unified_v1", "")
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
    _, backup = preparation.prepare_database(path, apply=True, offline_confirmed=True)
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
    assert preparation.prepare_database(path, apply=True, offline_confirmed=True) == ((), None)
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
    _, backup = preparation.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(backup.path) == before
    restored = tmp_path / "restored-relations.sqlite3"
    with closing(open_readonly(backup.path)) as source, closing(sqlite3.connect(restored)) as target:
        source.backup(target)
    assert signature(restored) == before
    preparation.prepare_database(restored, apply=True, offline_confirmed=True)
    for candidate in (path, restored):
        with closing(open_readonly(candidate)) as db:
            assert [tuple(row) for row in db.execute("SELECT * FROM memory_item_edit_history")] == history
            assert [tuple(row) for row in db.execute("SELECT * FROM memory_reconciliation_proposals")] == proposals
            assert db.execute("SELECT revision FROM memory_items WHERE id=5").fetchone()[0] == 4
            sequences = dict(db.execute("SELECT name,seq FROM sqlite_sequence"))
            assert sequences["memory_items"] == sequences["memory_item_edit_history"] == 20
            assert sequences["memory_reconciliation_proposals"] == 30
            assert inspect_connection(db) == ()  # Includes every restored index/constraint.
