"""Queue transitions remain independent of domain action commits."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hina_bot.core.admin_commands import enqueue_admin_command
from hina_bot.core.store import Store
from hina_bot.discord import admin_commands


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel", "finish_error"])
async def test_dispatch_preserves_commit_and_restart_contract(tmp_path, monkeypatch, outcome):
    path = tmp_path / "queue.sqlite3"
    store = Store.create(str(path))
    observer = sqlite3.connect(path)
    command_id, _ = enqueue_admin_command(
        path, request_id="d" * 32, action="note.set", payload={"text": "private payload"},
    )
    client = SimpleNamespace(store=store, events=SimpleNamespace(emit=Mock()))
    calls = []

    async def execute(client, command):
        calls.append(command.id)
        # The claim is visible before dispatch, and holds no write transaction.
        assert observer.execute("SELECT status FROM admin_commands").fetchone() == ("running",)
        assert not client.store.db.in_transaction
        client.store.set_note("manual", "applied")
        assert observer.execute("SELECT text FROM notes WHERE scope='manual'").fetchone() == (
            "applied",
        )
        if outcome == "error":
            raise ValueError("rejected after write")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return {"applied": True}

    monkeypatch.setattr(admin_commands, "execute_admin_command", execute)
    if outcome == "finish_error":
        store.db.execute(
            """CREATE TEMP TRIGGER reject_finish BEFORE UPDATE ON admin_commands
               WHEN NEW.status='succeeded'
               BEGIN SELECT RAISE(ABORT, 'finish failure'); END"""
        )
    try:
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await admin_commands.run_admin_command_once(client)
        elif outcome == "finish_error":
            with pytest.raises(sqlite3.IntegrityError, match="finish failure"):
                await admin_commands.run_admin_command_once(client)
        else:
            assert await admin_commands.run_admin_command_once(client)

        row = observer.execute(
            "SELECT status,payload_json,error_type,result_json FROM admin_commands"
        ).fetchone()
        if outcome in {"cancel", "finish_error"}:
            assert row[:3] == ("running", '{"text":"private payload"}', "")
            client.events.emit.assert_not_called()
        else:
            assert row[0] == ("failed" if outcome == "error" else "succeeded")
            assert row[1] == "{}"
            assert row[2] == ("ValueError" if outcome == "error" else "")
            assert row[3] == ('{}' if outcome == "error" else '{"applied":true}')
            assert client.events.emit.call_args.args[0] == (
                "admin.command_failed" if outcome == "error" else "admin.command_completed"
            )

        store.close()
        client.store = Store.create(str(path))
        admin_commands.initialize_admin_commands(client)
        assert not await admin_commands.run_admin_command_once(client)
        assert calls == [command_id]
        assert client.store.note("manual") == "applied"
        if outcome in {"cancel", "finish_error"}:
            assert observer.execute(
                "SELECT status,payload_json,error_type FROM admin_commands"
            ).fetchone() == ("failed", "{}", "InterruptedAdminCommand")
    finally:
        observer.close()
        client.store.close()
