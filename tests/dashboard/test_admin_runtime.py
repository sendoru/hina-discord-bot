import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from hina_bot.core.admin_commands import AdminCommand
from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.discord.admin_commands import execute_admin_command


@pytest.mark.asyncio
async def test_runtime_admin_command_uses_runtime_settings_and_side_effects():
    store = Store(":memory:")
    settings = RuntimeSettings(
        Settings(discord_token="test", channel_context_chars=6000),
        store,
    )
    recent = NS(budget=6000, clear_all=Mock())
    client = NS(settings=settings, recent=recent)

    result = await execute_admin_command(
        client,
        AdminCommand(
            id=1,
            request_id="a" * 32,
            actor="local-dashboard",
            action="runtime.set",
            target="channel_context_chars",
            payload={"key": "channel_context_chars", "value": "4200"},
        ),
    )
    assert settings.channel_context_chars == 4200
    assert recent.budget == 4200
    assert result["source"] == "db"

    result = await execute_admin_command(
        client,
        AdminCommand(
            id=2,
            request_id="b" * 32,
            actor="local-dashboard",
            action="runtime.reset",
            target="channel_context_chars",
            payload={"key": "channel_context_chars"},
        ),
    )
    assert settings.channel_context_chars == 6000
    assert recent.budget == 6000
    assert result["source"] == "startup"
    store.close()


def test_runtime_dashboard_post_only_enqueues_command(tmp_path):
    database = tmp_path / "hina.sqlite3"
    store = Store(str(database))
    RuntimeSettings(Settings(discord_token="test"), store)
    store.close()
    settings = DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "usage.jsonl"),
        event_log_path=str(tmp_path / "events.jsonl"),
        write_enabled=True,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/admin/runtime/set",
            data={
                "_csrf": app.state.admin_writer.csrf_token,
                "_request_id": "c" * 32,
                "key": "channel_context_chars",
                "value": "4300",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303

    db = sqlite3.connect(database)
    row = db.execute(
        "SELECT action,target,payload_json,status FROM admin_commands"
    ).fetchone()
    assert row == (
        "runtime.set",
        "channel_context_chars",
        '{"key":"channel_context_chars","value":"4300"}',
        "pending",
    )
    assert db.execute("SELECT COUNT(*) FROM runtime_config").fetchone()[0] == 0
    db.close()
