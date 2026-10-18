"""The actual start entrypoint owns startup capture and failed-start cleanup."""

import asyncio
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.db_connections import open_readonly
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.discord import runtime_entry
from hina_bot.tooling.prepare_database import prepare_database


@pytest.mark.parametrize("failure", [None, "llm", "bot", "snapshot"])
def test_runtime_startup_capture_and_cleanup(tmp_path, monkeypatch, failure):
    path = tmp_path / "runtime.sqlite3"
    prepare_database(path, apply=True, offline_confirmed=True)
    store = Store.open_existing(str(path))
    RuntimeSettings(
        Settings(discord_token="old", model="previous"), store
    ).capture_startup_snapshot()
    if failure == "snapshot":
        real_open = Store.open_existing

        def inject_snapshot_failure(cls, *args, **kwargs):
            opened = real_open(*args, **kwargs)
            opened.db.execute("""CREATE TEMP TRIGGER reject_snapshot
                BEFORE INSERT ON runtime_config_startup
                BEGIN SELECT RAISE(ABORT, 'snapshot failed'); END""")
            return opened

        monkeypatch.setattr(Store, "open_existing", classmethod(inject_snapshot_failure))
    store.close()
    settings = Settings(discord_token="test", db_path=str(path), model="new-base")
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    provider = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(runtime_entry, "create_provider_client", lambda *args: provider)
    resources = {}

    class FakeLLM:
        def __init__(self, settings, *, client, admin_db):
            resources["admin"] = admin_db
            if failure == "llm":
                raise RuntimeError("llm failed")
            self.admin_db = admin_db
            self.client = client

        async def close(self):
            await self.client.close()
            self.admin_db.close()

    class FakeBot:
        def __init__(self, settings, *, store, llm):
            resources["store"] = store
            if failure == "bot":
                raise RuntimeError("bot failed")
            self.store = store
            self.llm = llm
            self.closed = False

        def is_closed(self):
            return self.closed

        async def close(self):
            self.closed = True
            try:
                await self.llm.close()
            finally:
                self.store.close()

    def run(bot, token):
        assert token == "test"
        with closing(open_readonly(path)) as db:
            assert (
                db.execute("SELECT value FROM runtime_config_startup WHERE key='model'").fetchone()[
                    0
                ]
                == '"new-base"'
            )
        asyncio.run(bot.close())

    monkeypatch.setattr(runtime_entry, "LLM", FakeLLM)
    monkeypatch.setattr(runtime_entry.web_bot, "HinaClient", FakeBot)
    monkeypatch.setattr(runtime_entry, "run_client", run)
    if failure:
        with pytest.raises((RuntimeError, sqlite3.IntegrityError), match="failed"):
            runtime_entry.main()
    else:
        runtime_entry.main()
    provider.close.assert_awaited_once()
    with pytest.raises(sqlite3.ProgrammingError):
        resources["admin"].db.execute("SELECT 1")
    if "store" in resources:
        with pytest.raises(sqlite3.ProgrammingError):
            resources["store"].db.execute("SELECT 1")
    with closing(open_readonly(path)) as db:
        assert db.execute("SELECT value FROM runtime_config_startup WHERE key='model'").fetchone()[
            0
        ] == ('"previous"' if failure else '"new-base"')
