import pytest

from hina_bot.dashboard.config import DashboardSettings


def test_dashboard_settings_reuse_runtime_paths_with_dashboard_overrides(monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", "/runtime/hina.sqlite3")
    monkeypatch.setenv("USAGE_LOG_PATH", "/runtime/usage.jsonl")
    monkeypatch.setenv("EVENT_LOG_PATH", "/runtime/events.jsonl")
    monkeypatch.setenv("DASHBOARD_DATABASE_PATH", "/dashboard/hina.sqlite3")
    monkeypatch.setenv("DASHBOARD_HOST", "127.0.0.1")
    monkeypatch.setenv("DASHBOARD_PORT", "9001")

    settings = DashboardSettings.load()

    assert settings.database_path == "/dashboard/hina.sqlite3"
    assert settings.usage_log_path == "/runtime/usage.jsonl"
    assert settings.event_log_path == "/runtime/events.jsonl"
    assert settings.host == "127.0.0.1"
    assert settings.port == 9001


def test_dashboard_settings_reject_invalid_port(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PORT", "70000")
    with pytest.raises(ValueError):
        DashboardSettings.load()
