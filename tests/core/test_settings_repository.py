"""Persistence and failure contracts for the settings boundary."""

import sqlite3

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.settings_repository import SettingsRepository
from hina_bot.core.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store.create(str(tmp_path / "settings.sqlite3"))
    try:
        yield instance
    finally:
        instance.close()


def test_repository_construction_does_not_initialize_schema():
    db = sqlite3.connect(":memory:")
    try:
        SettingsRepository(db)
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
        assert not db.in_transaction
    finally:
        db.close()


def test_runtime_settings_construction_keeps_explicit_schema_and_empty_snapshot(store):
    before = list(store.db.iterdump())
    RuntimeSettings(Settings(discord_token="token", openai_api_key="key"), store)
    assert list(store.db.iterdump()) == before
    assert store.db.execute("SELECT COUNT(*) FROM runtime_config_startup").fetchone()[0] == 0


def test_startup_snapshot_insert_failure_preserves_previous_snapshot(store):
    base = Settings(discord_token="token", openai_api_key="key")
    RuntimeSettings(base, store).capture_startup_snapshot()
    before = [
        tuple(row) for row in store.db.execute("SELECT * FROM runtime_config_startup ORDER BY key")
    ]
    store.db.execute(
        """CREATE TRIGGER reject_startup_snapshot BEFORE INSERT ON runtime_config_startup
           BEGIN SELECT RAISE(ABORT, 'snapshot failure'); END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="snapshot failure"):
        RuntimeSettings(base, store).capture_startup_snapshot()

    assert [
        tuple(row) for row in store.db.execute("SELECT * FROM runtime_config_startup ORDER BY key")
    ] == before
    assert not store.db.in_transaction


def test_failed_runtime_write_does_not_change_effective_override(store):
    settings = RuntimeSettings(Settings(discord_token="token", openai_api_key="key"), store)
    settings.set_text("rag_mode", "v1")
    store.db.execute(
        """CREATE TRIGGER reject_runtime_write BEFORE INSERT ON runtime_config
           BEGIN SELECT RAISE(ABORT, 'write failure'); END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="write failure"):
        settings.set_text("rag_mode", "off")

    assert settings.rag_mode == "v1"
    assert store.settings.runtime_values()["rag_mode"] == '"v1"'
    assert not store.db.in_transaction
