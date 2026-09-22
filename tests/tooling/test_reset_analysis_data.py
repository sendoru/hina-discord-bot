import sqlite3

import pytest

from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.reset_analysis_data import (
    UnsafeAnalysisReset,
    build_reset_plan,
    reset_analysis_data,
)


def _seed_safe_database(path):
    store = Store(str(path))
    dm = Scope(None, 10, 100)
    guild = Scope(1, 20, 100, True)

    store.add(dm, 1, "remember this", "okay", name="User")
    turn_id = int(store.history(dm)[-1]["id"])
    store.save_summary(dm, "persistent summary", turn_id)
    store.save_memory_extraction_cursor(dm, turn_id)
    store.add_memory_item(
        dm,
        "사용자는 테스트용 사실을 기억해 달라고 했다.",
        kind="fact",
        disclosure="local",
        source_message_ids=("1",),
    )

    store.add_shared_call(guild, 2, "User", "public call")
    shared_id = int(
        store.db.execute(
            "SELECT id FROM shared_calls WHERE scope=?",
            (guild.conversation,),
        ).fetchone()["id"]
    )
    store.save_shared_summary(guild, "User", "shared summary", shared_id)

    with store.db:
        store.db.execute(
            "INSERT INTO memory_modes(scope,mode) VALUES (?,?)",
            ("global", "read_only"),
        )
        store.db.execute(
            "INSERT INTO chat_log_modes(scope,mode) VALUES (?,?)",
            ("global", "off"),
        )
        store.db.execute(
            "INSERT INTO notes(scope,text) VALUES (?,?)",
            ("dm:100:user:100", "manual note"),
        )
        store.db.execute(
            """INSERT INTO emoji_registry(alias,emoji_id,description,source_guild_id)
               VALUES (?,?,?,?)""",
            ("hina", "123", "test emoji", "1"),
        )
        store.db.execute(
            """CREATE TABLE IF NOT EXISTS runtime_config (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL,
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        store.db.execute(
            "INSERT INTO runtime_config(key,value) VALUES (?,?)",
            ("chat_web_search", "false"),
        )
    store.close()

    admin = AdminDatabase(str(path))
    with admin.transaction():
        admin.db.execute(
            """INSERT INTO instructions(id,text,enabled)
               VALUES (?,?,?)""",
            ("test.instruction", "persistent instruction", 1),
        )
        admin.db.execute(
            """INSERT INTO runtime_knowledge(
                   id,kind,content,keywords,subjects,awareness,timeline,enabled
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                "test.fact",
                "world_fact",
                "persistent knowledge",
                "[]",
                "[]",
                "public",
                "current",
                1,
            ),
        )
    admin.close()


def _write_telemetry(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    usage = logs / "usage.jsonl"
    exchange = logs / "discord-usage.jsonl"
    events = logs / "events.jsonl"
    files = (
        usage,
        logs / "usage.jsonl.1",
        exchange,
        logs / "discord-usage.jsonl.2",
        events,
        logs / "events.jsonl.3",
    )
    for index, path in enumerate(files):
        path.write_text(f'{{"row":{index}}}\n', encoding="utf-8")
    return usage, events, files


def test_reset_deletes_only_raw_analysis_data_and_records_epoch(tmp_path):
    database = tmp_path / "hina.sqlite3"
    _seed_safe_database(database)
    usage, events, telemetry = _write_telemetry(tmp_path)

    plan, result = reset_analysis_data(
        database,
        usage_log_path=str(usage),
        event_log_path=str(events),
        apply=True,
    )

    assert plan.safe
    assert result is not None
    assert result.epoch_id == 1
    assert set(result.deleted_files) == set(telemetry)
    assert all(not path.exists() for path in telemetry)

    db = sqlite3.connect(database)
    try:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM shared_calls").fetchone()[0] == 0

        preserved = {
            "memory_items": 1,
            "summaries": 1,
            "shared_summaries": 1,
            "memory_extraction_cursors": 1,
            "memory_modes": 1,
            "chat_log_modes": 1,
            "notes": 1,
            "emoji_registry": 1,
            "runtime_config": 1,
            "instructions": 1,
            "runtime_knowledge": 1,
        }
        for table, expected in preserved.items():
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == expected
        epoch = db.execute(
            "SELECT id,reset_at FROM observability_epochs"
        ).fetchone()
        assert epoch[0] == 1
        assert epoch[1] == result.reset_at
    finally:
        db.close()


def test_pending_persistent_memory_blocks_destructive_reset(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store(str(database))
    scope = Scope(None, 10, 100)
    store.add(scope, 1, "not committed yet", "reply")
    store.close()

    usage, events, telemetry = _write_telemetry(tmp_path)

    plan = build_reset_plan(
        database,
        usage_log_path=str(usage),
        event_log_path=str(events),
    )
    pending = {item.kind: item.turns for item in plan.pending}
    assert not plan.safe
    assert pending["personal_summary"] == 1
    assert pending["structured_memory"] == 1

    with pytest.raises(UnsafeAnalysisReset):
        reset_analysis_data(
            database,
            usage_log_path=str(usage),
            event_log_path=str(events),
            apply=True,
        )

    db = sqlite3.connect(database)
    try:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        epoch_table = db.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='table' AND name='observability_epochs'"""
        ).fetchone()[0]
        assert epoch_table == 0
    finally:
        db.close()
    assert all(path.exists() for path in telemetry)


def test_dry_run_and_repeated_empty_reset_are_safe(tmp_path):
    database = tmp_path / "hina.sqlite3"
    _seed_safe_database(database)
    usage, events, telemetry = _write_telemetry(tmp_path)

    plan, result = reset_analysis_data(
        database,
        usage_log_path=str(usage),
        event_log_path=str(events),
    )
    assert result is None
    assert plan.raw_rows == {"turns": 1, "shared_calls": 1}
    assert all(path.exists() for path in telemetry)

    _, first = reset_analysis_data(
        database,
        usage_log_path=str(usage),
        event_log_path=str(events),
        apply=True,
    )
    _, second = reset_analysis_data(
        database,
        usage_log_path=str(usage),
        event_log_path=str(events),
        apply=True,
    )
    assert first is not None
    assert second is not None
    assert first.epoch_id == 1
    assert second.epoch_id == 2
