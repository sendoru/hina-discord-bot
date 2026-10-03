import asyncio
import sqlite3
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from hina_bot.core.admin_commands import AdminCommand
from hina_bot.core.recent import RecentMessages
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.discord.admin_commands import execute_admin_command


@pytest.mark.asyncio
async def test_state_admin_commands_reuse_store_and_recent_side_effects():
    store = Store(":memory:")
    recent = RecentMessages()
    locks = {}

    def lock_for(key):
        return locks.setdefault(key, asyncio.Lock())

    client = NS(
        store=store,
        recent=recent,
        channel_lock=lambda scope: lock_for(("channel", scope.realm, scope.channel_id)),
        memory_lock=lambda scope: lock_for(("memory", scope.user_note)),
    )

    base = {"guild_id": 1, "channel_id": 10, "user_id": 100}
    result = await execute_admin_command(
        client,
        AdminCommand(
            id=1,
            request_id="a" * 32,
            actor="local-dashboard",
            action="memory.mode",
            target="guild:1:channel:10",
            payload={**base, "target": "channel", "mode": "read_only"},
        ),
    )
    assert result["mode"] == "read_only"
    assert store.memory_mode_override("guild:1:channel:10") == "read_only"

    result = await execute_admin_command(
        client,
        AdminCommand(
            id=2,
            request_id="b" * 32,
            actor="local-dashboard",
            action="chatlog.mode",
            target="guild:1:channel:10",
            payload={**base, "target": "channel", "mode": "direct"},
        ),
    )
    assert result["mode"] == "direct"
    assert store.chat_log_mode_override("guild:1:channel:10") == "on"
    assert store.note("config:chatlog_capture:guild:1:channel:10") == "direct"

    await execute_admin_command(
        client,
        AdminCommand(
            id=3,
            request_id="c" * 32,
            actor="local-dashboard",
            action="note.set",
            target="guild:1:user:100",
            payload={**base, "target": "user", "text": "관리 메모"},
        ),
    )
    assert store.note("guild:1:user:100") == "관리 메모"
    store.close()


def test_state_admin_post_requires_purge_confirmation_and_enqueues(tmp_path):
    database = tmp_path / "hina.sqlite3"
    Store(str(database)).close()
    settings = DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=True,
    )
    app = create_app(settings)
    common = {
        "_csrf": app.state.admin_writer.csrf_token,
        "target_scope_type": "guild",
        "target_guild_id": "1",
        "target_channel_id": "10",
        "target_user_id": "100",
        "target": "channel",
    }

    with TestClient(app) as client:
        rejected = client.post(
            "/state/admin",
            data={
                **common,
                "_request_id": "d" * 32,
                "action": "memory.purge",
            },
            follow_redirects=False,
        )
        assert rejected.status_code == 422

        queued = client.post(
            "/state/admin",
            data={
                **common,
                "_request_id": "e" * 32,
                "action": "memory.purge",
                "confirm": "yes",
            },
            follow_redirects=False,
        )
        assert queued.status_code == 303

    db = sqlite3.connect(database)
    row = db.execute(
        "SELECT action,target,status FROM admin_commands ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row == ("memory.purge", "guild:1:channel:10", "pending")
    db.close()
