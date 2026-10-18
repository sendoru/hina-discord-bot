import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from hina_bot.core.db_schema import SchemaError, initialize_connection
from hina_bot.dashboard import app as dashboard_app
from hina_bot.dashboard import repository as repository_module
from hina_bot.dashboard.config import DashboardSettings


def current_database(path):
    with closing(sqlite3.connect(path)) as db:
        initialize_connection(db)


def settings_for(path, *, writes=False):
    return DashboardSettings(
        database_path=str(path),
        usage_log_path=str(path.parent / "usage.jsonl"),
        event_log_path=str(path.parent / "events.jsonl"),
        write_enabled=writes,
    )


def fingerprint(path):
    stat = path.stat()
    return path.read_bytes(), stat.st_mtime_ns, stat.st_mode, sorted(path.parent.iterdir())


@pytest.mark.parametrize("writes", [False, True])
@pytest.mark.parametrize("sql", [
    "DROP TABLE schema_migrations",
    "DELETE FROM schema_migrations WHERE component='store'",
    "DELETE FROM schema_migrations WHERE component='settings'",
    "DELETE FROM schema_migrations WHERE component='admin'",
    "UPDATE schema_migrations SET version=2 WHERE component='store'",
    "UPDATE schema_migrations SET name='unknown' WHERE component='store'",
    "DROP TABLE failed_turns",
    "DROP TABLE memory_extraction_cursors",
    "DROP TABLE memory_item_edit_history",
    "DROP TABLE memory_reconciliation_proposals",
    "DROP TABLE shared_calls",
    "DROP TABLE runtime_config_startup",
    "DROP TABLE instructions",
    "ALTER TABLE turns RENAME COLUMN turn_id TO obsolete_turn_id",
    "ALTER TABLE turns RENAME COLUMN name TO obsolete_name",
    "ALTER TABLE failed_turns RENAME COLUMN turn_id TO obsolete_turn_id",
    "ALTER TABLE memory_items RENAME COLUMN user_name TO obsolete_user_name",
    "ALTER TABLE memory_items RENAME COLUMN status TO obsolete_status",
    "ALTER TABLE memory_items RENAME COLUMN superseded_by TO obsolete_superseded_by",
    "ALTER TABLE memory_items RENAME COLUMN revision TO obsolete_revision",
    "CREATE TRIGGER unexpected AFTER INSERT ON turns BEGIN SELECT 1; END",
])
def test_startup_rejects_unprepared_or_changed_schema_without_writes(tmp_path, sql, writes):
    path = tmp_path / "hina.sqlite3"
    current_database(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(sql)
    before = fingerprint(path)

    with pytest.raises(SchemaError):
        dashboard_app.create_app(settings_for(path, writes=writes))

    assert fingerprint(path) == before


def test_startup_rejects_view_instead_of_required_table(tmp_path):
    path = tmp_path / "hina.sqlite3"
    current_database(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE memory_items")
        db.execute("CREATE VIEW memory_items AS SELECT 1 AS id, 'active' AS status")
    before = fingerprint(path)
    with pytest.raises(SchemaError, match="store schema"):
        dashboard_app.create_app(settings_for(path))
    assert fingerprint(path) == before


@pytest.mark.parametrize("exists", [False, True])
def test_startup_never_creates_or_prepares_empty_database(tmp_path, exists):
    path = tmp_path / "hina.sqlite3"
    if exists:
        path.touch()
    before = sorted(tmp_path.iterdir())
    with pytest.raises(SchemaError if exists else FileNotFoundError):
        dashboard_app.create_app(settings_for(path))
    assert sorted(tmp_path.iterdir()) == before
    if exists:
        assert path.read_bytes() == b""


@pytest.mark.parametrize("epochs", [False, True])
def test_current_startup_and_queries_are_readonly_with_optional_epochs(tmp_path, monkeypatch, epochs):
    path = tmp_path / "hina.sqlite3"
    current_database(path)
    if epochs:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE observability_epochs (id INTEGER PRIMARY KEY, reset_at TEXT)")
            db.execute("INSERT INTO observability_epochs VALUES (1,'2026-10-18T00:00:00+00:00')")
            # Other offline maintenance metadata must not invalidate the business schema.
            db.execute("CREATE TABLE maintenance_audit (operation TEXT)")
    path.chmod(0o444)
    before = fingerprint(path)
    statements = []
    original_open = repository_module.open_readonly

    def traced_open(path):
        db = original_open(path)
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(repository_module, "open_readonly", traced_open)
    app = dashboard_app.create_app(settings_for(path))
    with TestClient(app) as client:
        for route in ["/healthz", "/memory", "/memory/cursors", "/reconciliation", "/summaries"]:
            assert client.get(route).status_code == 200
    assert bool(app.state.repository.observability_epochs()) is epochs
    assert statements
    assert all(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements)
    assert fingerprint(path) == before


def test_cli_explains_required_offline_preparation(tmp_path, monkeypatch):
    path = tmp_path / "hina.sqlite3"
    path.touch()
    monkeypatch.setattr(DashboardSettings, "load", lambda: settings_for(path))
    with pytest.raises(SystemExit, match="hina-prepare-db --dry-run"):
        dashboard_app.main()
    assert path.read_bytes() == b""
