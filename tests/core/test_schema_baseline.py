"""Current-only runtime, verified baseline adoption and ordered upgrade contracts."""

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from hina_bot.core import admin_db, db_schema
from hina_bot.core.db_connections import open_existing, open_readonly
from hina_bot.core.db_ddl import DDL, V1_TABLES
from hina_bot.core.db_schema import SchemaError, require_current
from hina_bot.core.store import Store
from hina_bot.tooling import db_migrations, prepare_database
from hina_bot.tooling.maintenance_backup import database_signature


def signature(path):
    with closing(open_readonly(path)) as db:
        return database_signature(db)


def operational_fixture(path):
    sql = Path(__file__).parents[1] / "fixtures" / "db-operational-v1.sql"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(sql.read_text())
        db.execute("INSERT INTO notes VALUES ('config:chatlog_unified_v1','1')")
        db.execute("INSERT INTO runtime_config(key,value) VALUES ('model','\"custom-model\"')")
        db.execute("INSERT INTO runtime_config_startup(key,value) VALUES ('model','\"old-base\"')")
        db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('memory_items',900)")
        db.commit()


def test_operational_baseline_changes_only_ledger_and_restores_exact_input(tmp_path):
    path = tmp_path / "operational.sqlite3"
    operational_fixture(path)
    before = signature(path)
    with pytest.raises(SchemaError, match="preparation required"):
        Store.open_existing(path)
    assert prepare_database.prepare_database(path)[0] == db_schema.COMPONENTS
    assert signature(path) == before
    changed, backup = prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    assert changed == db_schema.COMPONENTS
    assert signature(backup.path) == before
    with closing(open_readonly(path)) as db, closing(open_readonly(backup.path)) as old:
        old_schema = list(old.execute("SELECT type,name,tbl_name,sql FROM sqlite_master"))
        new_schema = [row for row in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master")
                      if row[2] != "schema_migrations"]
        assert [tuple(row) for row in new_schema] == [tuple(row) for row in old_schema]
        for table, in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            assert [tuple(row) for row in db.execute(f'SELECT * FROM "{table}"')] == [
                tuple(row) for row in old.execute(f'SELECT * FROM "{table}"')
            ]
        require_current(db)
    current = signature(path)
    assert prepare_database.prepare_database(path, apply=True, offline_confirmed=True) == ((), None)
    assert signature(path) == current
    restored = tmp_path / "restored.sqlite3"
    with closing(open_readonly(backup.path)) as source, closing(sqlite3.connect(restored)) as target:
        source.backup(target)
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert signature(restored) == before
    prepare_database.prepare_database(restored, apply=True, offline_confirmed=True)
    Store.open_existing(restored).close()


@pytest.mark.parametrize("change", ["check", "index", "trigger", "column", "ledger"])
def test_runtime_refuses_recorded_schema_drift(tmp_path, change):
    path = tmp_path / "drift.sqlite3"
    prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    with closing(open_existing(path)) as db, db:
        if change == "index":
            db.execute("CREATE UNIQUE INDEX extra_semantics ON notes(text COLLATE NOCASE)")
        elif change == "trigger":
            db.execute("CREATE TRIGGER extra_write AFTER INSERT ON notes BEGIN DELETE FROM notes; END")
        elif change == "column":
            db.execute("ALTER TABLE turns ADD COLUMN unknown TEXT")
        elif change == "ledger":
            db.execute("UPDATE schema_migrations SET version=99 WHERE component='store'")
        else:
            db.execute("DROP TABLE memory_modes")
            db.execute("CREATE TABLE memory_modes(scope TEXT PRIMARY KEY,mode TEXT NOT NULL "
                       "CHECK(mode IN ('NORMAL','READ_ONLY','WRITE_ONLY','OFF')))")
    before = signature(path)
    with pytest.raises(SchemaError):
        Store.open_existing(path)
    with pytest.raises(SchemaError):
        prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before


def test_normal_preparation_refuses_old_schema_without_backup_or_mutation(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE notes(scope TEXT PRIMARY KEY,text TEXT NOT NULL)")
    before = signature(path)
    with pytest.raises(SchemaError, match="hina-upgrade-legacy-db"):
        prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before and not (tmp_path / "backups").exists()


@pytest.mark.parametrize("stage", ["ledger", "commit"])
def test_baseline_failure_rolls_back_and_keeps_verified_backup(tmp_path, monkeypatch, stage):
    path = tmp_path / "failure.sqlite3"
    operational_fixture(path)
    before = signature(path)

    class FailingConnection(sqlite3.Connection):
        def commit(self):
            if stage == "commit":
                raise sqlite3.OperationalError("injected commit failure")
            super().commit()

    def fault_open(path):
        db = sqlite3.connect(path, factory=FailingConnection)
        if stage == "ledger":
            db.set_authorizer(lambda action, name, *rest:
                              sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT
                              and name == "schema_migrations" else sqlite3.SQLITE_OK)
        return db

    monkeypatch.setattr(prepare_database, "open_existing", fault_open)
    with pytest.raises(sqlite3.DatabaseError):
        prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1 and signature(backups[0]) == before


def test_baseline_rejects_change_after_backup(tmp_path, monkeypatch):
    path = tmp_path / "race.sqlite3"
    operational_fixture(path)
    real_backup = prepare_database.backup_schema_database

    def change_after_backup(*args, **kwargs):
        backup = real_backup(*args, **kwargs)
        with closing(open_existing(path)) as db, db:
            db.execute("INSERT INTO notes VALUES ('concurrent','keep')")
        return backup

    monkeypatch.setattr(prepare_database, "backup_schema_database", change_after_backup)
    with pytest.raises(SchemaError, match="changed since backup"):
        prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    with closing(open_readonly(path)) as db:
        assert db.execute("SELECT text FROM notes WHERE scope='concurrent'").fetchone()[0] == "keep"
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone() is None


@pytest.mark.parametrize("start_version", [1, 2])
def test_explicit_next_versions_run_in_order_and_roll_back_together(
    tmp_path, monkeypatch, start_version,
):
    path = tmp_path / "ordered.sqlite3"
    prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    # Test-only v2/v3 add metadata, not any #399-#401 feature.
    with closing(sqlite3.connect(":memory:")) as target:
        for sql in DDL["store"]:
            target.execute(sql)
        target.execute("CREATE INDEX test_v2 ON notes(text)")
        v2_signature = db_schema.schema_signature(target, V1_TABLES["store"])
        target.execute("CREATE INDEX test_v3 ON notes(text,scope)")
        final_signature = db_schema.schema_signature(target, V1_TABLES["store"])
    monkeypatch.setitem(db_schema.HISTORY, "store",
                        (*db_schema.HISTORY["store"], "test-v2", "test-v3"))
    monkeypatch.setitem(db_schema.SCHEMA_SIGNATURES["store"], 3, {final_signature})
    monkeypatch.setitem(db_schema.SCHEMA_SIGNATURES["store"], 2, {v2_signature})
    if start_version == 2:
        with closing(open_existing(path)) as db, db:
            db.execute("CREATE INDEX test_v2 ON notes(text)")
            db.execute("INSERT INTO schema_migrations(component,version,name) VALUES ('store',2,'test-v2')")
    called = []

    def v2(db):
        called.append(2)
        db.execute("CREATE INDEX test_v2 ON notes(text)")

    def v3_fail(db):
        called.append(3)
        raise sqlite3.OperationalError("step failed")

    monkeypatch.setattr(db_migrations, "UPGRADES", (("store", 2, v2), ("store", 3, v3_fail)))
    before = signature(path)
    with closing(open_existing(path)) as db:
        with pytest.raises(sqlite3.OperationalError):
            db_migrations.prepare_connection(db)
        assert not db.in_transaction
    assert called == ([2, 3] if start_version == 1 else [3]) and signature(path) == before

    def v3(db):
        db.execute("CREATE INDEX test_v3 ON notes(text,scope)")

    monkeypatch.setattr(db_migrations, "UPGRADES", (("store", 2, v2), ("store", 3, v3)))
    with closing(open_existing(path)) as db:
        assert db_migrations.prepare_connection(db) == ("store",)
        assert db_migrations.prepare_connection(db) == ()
        assert [tuple(row) for row in db.execute(
            "SELECT version,name FROM schema_migrations WHERE component='store' ORDER BY version"
        )] == [
                              (1, "store-baseline-v1"), (2, "test-v2"), (3, "test-v3")]
        db.execute("DELETE FROM schema_migrations WHERE component='store' AND version=1")
        with pytest.raises(SchemaError, match="incomplete"):
            db_schema.schema_versions(db)


def test_runtime_imports_neither_migration_nor_legacy_tool():
    code = """
import sys
from hina_bot.discord import runtime_entry
from hina_bot.core.store import Store
from hina_bot.core.admin_db import AdminDatabase
Store.create(':memory:').close()
AdminDatabase.copy_to_memory().close()
assert 'hina_bot.tooling.legacy_database' not in sys.modules
assert 'hina_bot.tooling.db_migrations' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)


def test_eval_copy_validates_the_copied_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "copy-source.sqlite3"
    prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    before = signature(path)

    class ChangedCopy(sqlite3.Connection):
        def backup(self, target, **kwargs):
            super().backup(target, **kwargs)
            # The copied snapshot must be checked, not only an earlier source read.
            target.execute("ALTER TABLE instructions ADD COLUMN incompatible TEXT")

    monkeypatch.setattr(admin_db, "open_readonly", lambda path:
                        sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True,
                                        factory=ChangedCopy))
    with pytest.raises(SchemaError):
        admin_db.AdminDatabase.copy_to_memory(path)
    assert signature(path) == before
