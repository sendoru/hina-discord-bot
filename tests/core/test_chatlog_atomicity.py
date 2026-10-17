"""Unified chatlog settings commit together before any ephemeral side effects."""

import asyncio
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from hina_bot.core.admin_commands import enqueue_admin_command
from hina_bot.core.chatlog_modes import set_unified_chatlog_mode
from hina_bot.core.recent import RecentMessages
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.admin_commands import run_admin_command_once
from hina_bot.discord.chatlog_commands import ChatLogCommands


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "chatlog.sqlite3"
    first, scope = Store.create(path), Scope(1, 10, 100)
    second = Store.open_existing(path)
    set_unified_chatlog_mode(first, scope.channel, "off")
    first.set_note("manual", "keep")
    try:
        yield path, first, second, scope
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("mode", ["all", "direct", "off", None])
@pytest.mark.parametrize("target", ["chat_log_modes", "notes"])
def test_either_statement_failure_rolls_back_both_overrides(stores, mode, target):
    _, first, second, scope = stores
    set_unified_chatlog_mode(first, scope.channel, "direct" if mode == "off" else "off")
    before = list(second.db.iterdump())
    action = "DELETE" if mode is None else "INSERT"
    first.db.execute(f"""CREATE TEMP TRIGGER reject_setting BEFORE {action} ON {target}
                         BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        set_unified_chatlog_mode(first, scope.channel, mode)
    assert list(second.db.iterdump()) == before
    assert not first.db.in_transaction


def test_observer_cannot_see_the_mode_without_its_capture_setting(stores):
    _, first, second, scope = stores
    observed = []

    def observe():
        observed.append((second.chat_log_mode_override(scope.channel),
                         second.note("config:chatlog_capture:" + scope.channel)))
        return 0

    first.db.create_function("observe_chatlog", 0, observe)
    first.db.execute("""CREATE TEMP TRIGGER observe_capture AFTER INSERT ON notes
                        BEGIN SELECT observe_chatlog(); END""")
    set_unified_chatlog_mode(first, scope.channel, "direct")
    assert observed == [("off", "all")]
    assert second.chat_log_mode_override(scope.channel) == "on"
    assert second.note("config:chatlog_capture:" + scope.channel) == "direct"
    assert second.note("manual") == "keep"


def test_existing_transaction_is_neither_committed_nor_rolled_back(stores):
    _, first, second, scope = stores
    first.db.execute("BEGIN IMMEDIATE")
    first.db.execute("INSERT INTO notes VALUES ('uncommitted','keep')")
    try:
        with pytest.raises(RuntimeError, match="transaction"):
            set_unified_chatlog_mode(first, scope.channel, "direct")
        assert first.db.in_transaction
        assert first.note("uncommitted") == "keep"
        assert second.note("uncommitted") == ""
    finally:
        first.db.rollback()


@pytest.mark.parametrize("mode", ["all", "direct", "off", None])
def test_commit_failure_preserves_the_previous_complete_setting(tmp_path, mode):
    class Connection(sqlite3.Connection):
        fail_commit = False

        def commit(self):
            if self.fail_commit:
                raise sqlite3.OperationalError("commit failed")
            super().commit()

    path = tmp_path / "commit.sqlite3"
    Store.create(path).close()
    connection = sqlite3.connect(path, factory=Connection)
    connection.row_factory = sqlite3.Row
    first, second = Store(connection), Store.open_existing(path)
    try:
        set_unified_chatlog_mode(first, "global", "direct" if mode == "off" else "off")
        before = list(second.db.iterdump())
        connection.fail_commit = True
        with pytest.raises(sqlite3.OperationalError, match="commit failed"):
            set_unified_chatlog_mode(first, "global", mode)
        assert list(second.db.iterdump()) == before
        assert not first.db.in_transaction
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["discord", "queue"])
async def test_db_failure_keeps_buffers_hydration_and_does_not_report_success(stores, entrypoint):
    path, first, second, scope = stores
    first.db.execute("""CREATE TEMP TRIGGER reject_capture BEFORE INSERT ON notes
                        BEGIN SELECT RAISE(ABORT, 'private-secret'); END""")
    recent = RecentMessages()
    recent.add(scope, 1, "name", "buffer")
    recent.mark_hydrated(scope)
    before = recent.context(scope, 10)
    lock = asyncio.Lock()
    client = NS(store=first, recent=recent, channel_lock=lambda scope: lock,
                events=NS(emit=Mock()))
    if entrypoint == "discord":
        group = ChatLogCommands(client)
        interaction = NS(guild_id=1, channel_id=10, user=NS(id=100),
                         response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        with pytest.raises(sqlite3.IntegrityError):
            await group.mode.callback(group, interaction, "direct", "channel")
        interaction.followup.send.assert_not_awaited()
    else:
        enqueue_admin_command(path, request_id="a" * 32, action="chatlog.mode",
                              payload={"guild_id": 1, "channel_id": 10, "user_id": 100,
                                       "target": "channel", "mode": "direct"})
        assert await run_admin_command_once(client)
        assert second.db.execute("SELECT status FROM admin_commands").fetchone()[0] == "failed"
        assert client.events.emit.call_args.args[0] == "admin.command_failed"
        assert "private-secret" not in str(client.events.emit.call_args_list)
    assert recent.context(scope, 10) == before
    assert not recent.needs_hydration(scope)
    assert second.chat_log_mode_override(scope.channel) == "off"


@pytest.mark.asyncio
async def test_buffer_failure_is_reported_after_the_setting_has_committed(stores):
    path, first, second, scope = stores
    lock = asyncio.Lock()
    client = NS(store=first, recent=NS(clear_channel=Mock(side_effect=RuntimeError("buffer failed"))),
                channel_lock=lambda scope: lock, events=NS(emit=Mock()))
    enqueue_admin_command(path, request_id="b" * 32, action="chatlog.mode",
                          payload={"guild_id": 1, "channel_id": 10, "user_id": 100,
                                   "target": "channel", "mode": "direct"})
    assert await run_admin_command_once(client)
    assert second.chat_log_mode_override(scope.channel) == "on"
    assert second.note("config:chatlog_capture:" + scope.channel) == "direct"
    assert second.db.execute("SELECT status FROM admin_commands").fetchone()[0] == "failed"
