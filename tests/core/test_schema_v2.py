"""Store v2 (#451): memory_item_annotations via the explicit offline upgrade."""

import sqlite3
from contextlib import closing

import pytest

from hina_bot.core import db_schema
from hina_bot.core.db_connections import open_existing, open_readonly
from hina_bot.core.db_ddl import LEDGER_DDL, V1_DDL
from hina_bot.core.db_schema import SchemaError
from hina_bot.core.store import Store
from hina_bot.tooling import db_migrations, prepare_database
from hina_bot.tooling.maintenance_backup import database_signature


def signature(path):
    with closing(open_readonly(path)) as db:
        return database_signature(db)


def v1_database(path):
    """A prepared v1 file as deployed before store v2, with some rows."""
    with closing(sqlite3.connect(path)) as db:
        for statements in V1_DDL.values():
            for statement in statements:
                db.execute(statement)
        db.execute("INSERT INTO notes(scope,text) VALUES ('config:chatlog_unified_v1','1')")
        db.execute(LEDGER_DDL)
        for component in V1_DDL:
            db.execute("INSERT INTO schema_migrations(component,version,name) VALUES (?,?,?)",
                       (component, 1, component + "-baseline-v1"))
        db.execute(
            "INSERT INTO memory_items(id,user_id,content,kind,origin_realm,origin_channel_id,"
            "origin_public_at_capture,disclosure,revision) "
            "VALUES (41,'u1','keeps-content','fact','dm:u1','c1',0,'local',3)"
        )
        db.execute("UPDATE sqlite_sequence SET seq=900 WHERE name='memory_items'")
        db.commit()


def test_v1_store_requires_offline_upgrade_and_keeps_every_row(tmp_path):
    path = tmp_path / "v1.sqlite3"
    v1_database(path)
    before = signature(path)
    with pytest.raises(SchemaError, match="preparation required"):
        Store.open_existing(str(path))
    assert prepare_database.prepare_database(path)[0] == ("store",)
    assert signature(path) == before

    changed, backup = prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    assert changed == ("store",)
    assert signature(backup.path) == before
    with closing(open_readonly(path)) as db:
        row = db.execute("SELECT id,content,revision FROM memory_items").fetchone()
        assert tuple(row) == (41, "keeps-content", 3)
        assert db.execute("SELECT seq FROM sqlite_sequence WHERE name='memory_items'").fetchone()[0] == 900
        assert db.execute("SELECT COUNT(*) FROM memory_item_annotations").fetchone()[0] == 0
        assert [tuple(r) for r in db.execute(
            "SELECT component,version,name FROM schema_migrations ORDER BY component,version"
        )] == [("admin", 1, "admin-baseline-v1"), ("settings", 1, "settings-baseline-v1"),
               ("store", 1, "store-baseline-v1"), ("store", 2, "store-memory-annotations-v2")]
    Store.open_existing(str(path)).close()
    current = signature(path)
    assert prepare_database.prepare_database(path, apply=True, offline_confirmed=True) == ((), None)
    assert signature(path) == current


def test_failed_v2_step_rolls_back_to_v1(tmp_path, monkeypatch):
    path = tmp_path / "v1-fail.sqlite3"
    v1_database(path)
    before = signature(path)

    def broken(db):
        db_migrations.add_memory_item_annotations(db)
        raise sqlite3.OperationalError("injected v2 failure")

    monkeypatch.setattr(db_migrations, "UPGRADES", (("store", 2, broken),))
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    assert signature(path) == before
    with closing(open_readonly(path)) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_item_annotations'").fetchone() is None
        assert db_schema.schema_versions(db)["store"] == 1


def test_fresh_store_is_created_at_v2(tmp_path):
    store = Store.create(str(tmp_path / "fresh.sqlite3"))
    try:
        versions = db_schema.schema_versions(store.db)
        assert versions["store"] == len(db_schema.HISTORY["store"]) == 2
        assert store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_item_annotations'"
        ).fetchone()
    finally:
        store.close()


def test_v2_signature_refuses_annotation_table_drift(tmp_path):
    path = tmp_path / "drift.sqlite3"
    prepare_database.prepare_database(path, apply=True, offline_confirmed=True)
    with closing(open_existing(path)) as db, db:
        db.execute("ALTER TABLE memory_item_annotations ADD COLUMN unknown TEXT")
    with pytest.raises(SchemaError):
        Store.open_existing(str(path))


def _insert(db, **overrides):
    row = {
        "memory_item_id": 1, "item_revision": 0, "time_scope": "ongoing", "stability": "long",
        "observed_at": "2026-10-30T12:00:00+09:00", "usage_mode": "ambient",
        "directive_type": None, "audience": "owner", "last_confirmed_at": "2026-10-30T12:00:00+09:00",
        "annotator": "extractor", "annotator_version": "test",
    }
    row.update(overrides)
    columns = ",".join(row)
    db.execute(f"INSERT INTO memory_item_annotations({columns}) VALUES ({','.join('?' for _ in row)})",
               tuple(row.values()))


@pytest.mark.parametrize("overrides", [
    {"usage_mode": "directive"},  # directive without directive_type
    {"directive_type": "boundary"},  # directive_type without directive
    {"usage_mode": "sensitive", "audience": "server"},
    {"time_scope": "past"},  # stability is ongoing-only
    {"proactive_count": 3},
    {"importance": 4},
    {"annotator": "model"},
    {"time_scope": "someday", "stability": None},
])
def test_annotation_constraints_reject_inconsistent_rows(overrides):
    store = Store.create(":memory:")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(store.db, **overrides)
    finally:
        store.close()


def test_annotation_constraints_accept_consistent_rows():
    store = Store.create(":memory:")
    try:
        _insert(store.db)
        _insert(store.db, memory_item_id=2, usage_mode="directive", directive_type="boundary")
        _insert(store.db, memory_item_id=3, time_scope="planned", stability=None,
                event_at="2026-11-03", audience="server", usage_mode="contextual")
        assert store.db.execute("SELECT COUNT(*) FROM memory_item_annotations").fetchone()[0] == 3
    finally:
        store.close()
