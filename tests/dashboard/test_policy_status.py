from fastapi.testclient import TestClient

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.dashboard.app import create_app
from hina_bot.dashboard.config import DashboardSettings
from hina_bot.dashboard.repository import AdminRepository
from hina_bot.dashboard.services import ContextStateService


def test_global_policy_defaults_and_scoped_overrides(tmp_path):
    database = tmp_path / "policy.sqlite3"
    Store(str(database)).close()
    service = ContextStateService(AdminRepository(database))

    assert service.global_policy() == {
        "memory_mode": "normal",
        "memory_reads": True,
        "memory_writes": True,
        "memory_source": "default",
        "recent_enabled": "on",
        "recent_capture": "all",
        "recent_mode": "all",
        "recent_context_on": True,
        "recent_enabled_source": "default",
        "recent_capture_source": "default",
        "memory_scope_overrides": 0,
        "recent_scope_overrides": 0,
    }

    store = Store(str(database))
    guild = Scope(1, 10, 100)
    store.set_memory_mode_override("global", "read_only")
    store.set_memory_mode_override(guild.channel, "off")
    store.set_chat_log_mode_override("global", "off")
    store.set_chat_log_mode_override(guild.realm, "on")
    store.set_note("config:chatlog_capture:global", "direct")
    store.set_note(f"config:chatlog_capture:{guild.channel}", "all")
    store.close()

    policy = service.global_policy()
    assert policy["memory_reads"] is True
    assert policy["memory_writes"] is False
    assert policy["memory_mode"] == "read_only"
    assert policy["memory_source"] == "global override"
    assert policy["memory_scope_overrides"] == 1
    assert policy["recent_context_on"] is False
    assert policy["recent_mode"] == "off"
    assert policy["recent_enabled_source"] == "global override"
    assert policy["recent_capture_source"] == "global override"
    assert policy["recent_scope_overrides"] == 2

    scoped = service.context_state(
        target_scope_type="guild",
        target_guild_id="1",
        target_channel_id="10",
        target_user_id="100",
    )
    assert scoped["global_policy"] == policy
    assert scoped["effective"]["memory"]["reads"] is False
    assert scoped["effective"]["memory"]["writes"] is False
    assert scoped["effective"]["chat_log"]["effective"] == "all"


def test_overview_and_state_show_baseline_and_effective_status(tmp_path):
    database = tmp_path / "policy.sqlite3"
    store = Store(str(database))
    guild = Scope(1, 10, 100)
    dm = Scope(None, 20, 100)
    store.add(guild, 1, "guild question", "guild reply")
    store.add(dm, 2, "dm question", "dm reply")
    store.set_memory_mode_override("global", "write_only")
    store.set_memory_mode_override(guild.channel, "read_only")
    store.set_chat_log_mode_override("global", "off")
    store.set_chat_log_mode_override(guild.channel, "on")
    store.set_note(f"config:chatlog_capture:{guild.channel}", "direct")
    store.close()
    app = create_app(DashboardSettings(
        database_path=str(database),
        usage_log_path=str(tmp_path / "missing-usage.jsonl"),
        event_log_path=str(tmp_path / "missing-events.jsonl"),
    ))

    with TestClient(app) as client:
        overview = client.get("/")
        assert overview.status_code == 200
        assert 'id="global-context-policy"' in overview.text
        assert "Memory &amp; Context" in overview.text
        assert "<strong>OFF</strong>" in overview.text
        assert "<strong>ON</strong>" in overview.text
        assert "write_only · global override" in overview.text

        state = client.get(
            "/state?target_scope_type=guild&target_guild_id=1"
            "&target_channel_id=10&target_user_id=100"
        )
        assert state.status_code == 200
        assert 'id="global-context-policy"' in state.text
        assert 'id="state-target"' in state.text
        assert "direct · enabled from channel · capture from channel" in state.text
        assert "Memory reads" in state.text

        dm_state = client.get(
            "/state?target_scope_type=dm&target_user_id=100&target_dm_channel_id=20"
        )
        assert dm_state.status_code == 200
        assert "DM에서는 최근 채널 문맥을 사용하지 않습니다." in dm_state.text
        assert "off · enabled from global · capture from default" in dm_state.text
