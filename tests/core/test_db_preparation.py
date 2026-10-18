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
from hina_bot.core.db_connections import open_readonly
from hina_bot.core.db_schema import (
    SchemaError,
)
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.tooling import prepare_database as preparation
from hina_bot.tooling.maintenance_backup import database_signature
from hina_bot.tooling.runtime_migration import migrate


def signature(path):
    with closing(open_readonly(path)) as db:
        return database_signature(db)






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










@pytest.mark.parametrize("exists", [False, True, "store-only"])
def test_json_dry_run_uses_clone_and_keeps_target_untouched(tmp_path, exists):
    path = tmp_path / "target" / "admin.sqlite3"
    if exists:
        path.parent.mkdir()
        if exists == "store-only":
            Store.create(path).close()
        else:
            preparation.prepare_database(path, apply=True, offline_confirmed=True)
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
