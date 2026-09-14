from pathlib import Path

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store


def _base(**overrides):
    values = {"api_key": "key", "discord_token": "token"}
    values.update(overrides)
    return Settings(**values)


def test_settings_load_external_context_policy_defaults_to_bot_interactions_only(
    monkeypatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.delenv("EXTERNAL_CONTEXT_POLICY", raising=False)
    assert Settings.load().external_context_policy == "bot_interactions_only"


def test_settings_load_normalizes_legacy_external_context_policy(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("EXTERNAL_CONTEXT_POLICY", "direct_party_only")
    assert Settings.load().external_context_policy == "bot_interactions_only"


def test_settings_load_accepts_full_external_context_policy(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("EXTERNAL_CONTEXT_POLICY", "full")
    assert Settings.load().external_context_policy == "full"


def test_runtime_external_context_policy_override_round_trip():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(), store)
        assert settings.external_context_policy == "bot_interactions_only"
        assert settings.set_text("EXTERNAL_CONTEXT_POLICY", "full") == "full"
        assert settings.external_context_policy == "full"
        assert settings.source("EXTERNAL_CONTEXT_POLICY") == "db"
        assert settings.reset("EXTERNAL_CONTEXT_POLICY") == "bot_interactions_only"
        assert settings.external_context_policy == "bot_interactions_only"
    finally:
        store.close()


@pytest.mark.parametrize("value", ["", "direct", "strict", "private", "unknown"])
def test_external_context_policy_rejects_unknown_values(value: str):
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(), store)
        with pytest.raises(ValueError):
            settings.set_text("EXTERNAL_CONTEXT_POLICY", value)
    finally:
        store.close()
