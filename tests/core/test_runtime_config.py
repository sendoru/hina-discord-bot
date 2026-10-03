from pathlib import Path

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store


def _base(**overrides):
    values = {
        "openai_api_key": "key",
        "discord_token": "token",
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_requires_keyword_only_construction():
    with pytest.raises(TypeError):
        Settings("key", "token")


def test_settings_api_keys_are_provider_specific():
    settings = Settings(
        discord_token="token",
        openai_api_key="openai-key",
        gemini_api_key="gemini-key",
        openrouter_api_key="openrouter-key",
    )

    assert not hasattr(settings, "api_key")
    assert settings.api_key_for("openai") == "openai-key"
    assert settings.api_key_for("gemini") == "gemini-key"
    assert settings.api_key_for("openrouter") == "openrouter-key"


def test_settings_load_uses_code_defaults_when_runtime_env_is_absent(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    for name in (
        "CALL_PREFIXES",
        "DM_ALWAYS_REPLY",
        "ALWAYS_REPLY_CHANNEL_IDS",
        "PUBLIC_SERVER_MEMORY_IN_DM",
        "CHAT_WEB_SEARCH",
        "COMMUNITY_LORE",
        "MAX_OUTPUT_TOKENS",
        "MODEL_ROUTING_MODE",
        "MODEL_ROUTING_SMART_THRESHOLD",
        "MEMORY_ROUTING_SMART_THRESHOLD",
        "LLM_FAST_MODEL",
        "LLM_SMART_MODEL",
        "FAST_MAX_OUTPUT_TOKENS",
        "SMART_MAX_OUTPUT_TOKENS",
        "ROUTING_CLASSIFIER_MODE",
        "ROUTING_CLASSIFIER_PROVIDER",
        "ROUTING_CLASSIFIER_MODEL",
        "ROUTING_CLASSIFIER_API_KEY",
        "ROUTING_CLASSIFIER_TIMEOUT_SECONDS",
        "ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS",
        "GEMINI_THINKING_LEVEL",
        "GEMINI_FAST_THINKING_LEVEL",
        "GEMINI_SMART_THINKING_LEVEL",
        "GEMINI_STORE_INTERACTIONS",
        "CHANNEL_CONTEXT_CHARS",
        "HISTORY_MAX_CHARS",
        "LORE_MAX_ITEMS",
        "LORE_MAX_CHARS",
        "RUNTIME_DEFAULT_LOCATION",
        "EVENT_LOG_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.load()
    assert settings.call_prefixes == ("히나야",)
    assert settings.dm_always_reply is False
    assert settings.always_reply_channel_ids == frozenset()
    assert settings.public_memory_in_dm is True
    assert settings.chat_web_search is True
    assert settings.community_lore is True
    assert settings.output_tokens == 1000
    assert settings.model_routing_mode == "fixed"
    assert settings.model_routing_smart_threshold == pytest.approx(2.0)
    assert settings.memory_routing_smart_threshold == pytest.approx(2.0)
    assert settings.fast_model == settings.model
    assert settings.smart_model == settings.model
    assert settings.fast_output_tokens == 4096
    assert settings.smart_output_tokens == 8192
    assert settings.gemini_store_interactions is False
    assert settings.routing_classifier_mode == "off"
    assert settings.routing_classifier_provider == "openai"
    assert settings.routing_classifier_model == settings.fast_model
    assert settings.routing_classifier_timeout_seconds == pytest.approx(4.0)
    assert settings.routing_classifier_max_output_tokens == 256
    assert settings.channel_context_chars == 6000
    assert settings.history_max_chars == 12000
    assert settings.lore_max_items == 6
    assert settings.lore_max_chars == 3200
    assert settings.runtime_default_location == ""
    assert settings.event_log_path == "data/logs/events.jsonl"


def test_settings_loads_always_reply_channel_ids(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("ALWAYS_REPLY_CHANNEL_IDS", "10, 20, 10")

    settings = Settings.load()

    assert settings.always_reply_channel_ids == frozenset({10, 20})


def test_settings_loads_adaptive_model_tiers(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "fallback")
    monkeypatch.setenv("MODEL_ROUTING_MODE", "adaptive")
    monkeypatch.setenv("MODEL_ROUTING_SMART_THRESHOLD", "1.75")
    monkeypatch.setenv("MEMORY_ROUTING_SMART_THRESHOLD", "2.25")
    monkeypatch.setenv("LLM_FAST_MODEL", "gemini-fast")
    monkeypatch.setenv("LLM_SMART_MODEL", "gemini-smart")
    monkeypatch.setenv("FAST_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("SMART_MAX_OUTPUT_TOKENS", "10000")
    monkeypatch.setenv("GEMINI_FAST_THINKING_LEVEL", "minimal")
    monkeypatch.setenv("GEMINI_SMART_THINKING_LEVEL", "high")
    monkeypatch.setenv("GEMINI_STORE_INTERACTIONS", "true")

    value = Settings.load()
    assert value.model_routing_mode == "adaptive"
    assert value.model_routing_smart_threshold == pytest.approx(1.75)
    assert value.memory_routing_smart_threshold == pytest.approx(2.25)
    assert value.fast_model == "gemini-fast"
    assert value.smart_model == "gemini-smart"
    assert value.fast_output_tokens == 4096
    assert value.smart_output_tokens == 10000
    assert value.gemini_fast_thinking_level == "minimal"
    assert value.gemini_smart_thinking_level == "high"
    assert value.gemini_store_interactions is True
    assert not hasattr(value, "gemini_fast_total_output_tokens")
    assert not hasattr(value, "gemini_smart_total_output_tokens")


def test_settings_loads_separate_routing_classifier_provider_and_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-main")
    monkeypatch.setenv("LLM_FAST_MODEL", "gemini-fast")
    monkeypatch.setenv("ROUTING_CLASSIFIER_MODE", "shadow")
    monkeypatch.setenv("ROUTING_CLASSIFIER_PROVIDER", "openai")
    monkeypatch.setenv("ROUTING_CLASSIFIER_MODEL", "gpt-classifier")
    monkeypatch.setenv("ROUTING_CLASSIFIER_API_KEY", "separate-key")
    monkeypatch.setenv("ROUTING_CLASSIFIER_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS", "128")

    value = Settings.load()

    assert value.routing_classifier_mode == "shadow"
    assert value.routing_classifier_provider == "openai"
    assert value.routing_classifier_model == "gpt-classifier"
    assert value.routing_classifier_key() == "separate-key"
    assert value.routing_classifier_timeout_seconds == pytest.approx(3.5)
    assert value.routing_classifier_max_output_tokens == 128


def test_cross_provider_classifier_requires_explicit_model_when_enabled(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("GEMINI_API_KEY", "primary-key")
    monkeypatch.setenv("OPENAI_API_KEY", "classifier-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-main")
    monkeypatch.setenv("ROUTING_CLASSIFIER_MODE", "active")
    monkeypatch.setenv("ROUTING_CLASSIFIER_PROVIDER", "openai")
    monkeypatch.delenv("ROUTING_CLASSIFIER_MODEL", raising=False)

    with pytest.raises(ValueError, match="ROUTING_CLASSIFIER_MODEL"):
        Settings.load()


@pytest.mark.parametrize("threshold", ["0", "10.1", "nan", "inf", "not-a-number"])
@pytest.mark.parametrize(
    "variable",
    ["MODEL_ROUTING_SMART_THRESHOLD", "MEMORY_ROUTING_SMART_THRESHOLD"],
)
def test_settings_load_rejects_invalid_smart_threshold(
    monkeypatch, tmp_path: Path, threshold: str, variable: str
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv(variable, threshold)

    with pytest.raises(ValueError):
        Settings.load()


def test_runtime_settings_fall_back_to_code_defaults_without_db_override():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(), store)
        assert settings.model == "gpt-4.1-mini"
        assert settings.fast_model == "gpt-4.1-mini"
        assert settings.smart_model == "gpt-4.1-mini"
        assert settings.call_prefixes == ("히나야",)
        assert settings.dm_always_reply is False
        assert settings.always_reply_channel_ids == frozenset()
        assert settings.public_memory_in_dm is True
        assert settings.chat_web_search is True
        assert settings.community_lore is True
        assert settings.output_tokens == 1000
        assert settings.gemini_thinking_level == "low"
        assert settings.gemini_fast_thinking_level == "minimal"
        assert settings.gemini_smart_thinking_level == "medium"
        assert settings.gemini_store_interactions is False
        assert settings.model_routing_smart_threshold == pytest.approx(2.0)
        assert settings.memory_routing_smart_threshold == pytest.approx(2.0)
        assert settings.channel_context_chars == 6000
        assert settings.history_max_chars == 12000
        assert settings.lore_max_items == 6
        assert settings.lore_max_chars == 3200
        assert settings.runtime_default_location == ""
    finally:
        store.close()



def test_runtime_settings_persist_startup_snapshot_for_dashboard(tmp_path: Path):
    db = tmp_path / "runtime.sqlite3"
    store = Store(str(db))
    try:
        RuntimeSettings(
            _base(
                model="startup-model",
                channel_context_chars=4321,
                runtime_default_location="Seoul",
            ),
            store,
        )
        rows = {
            row["key"]: row["value"]
            for row in store.db.execute(
                "SELECT key,value FROM runtime_config_startup"
            ).fetchall()
        }
        assert set(rows) == set(__import__(
            "hina_bot.core.runtime_config",
            fromlist=["RUNTIME_SETTING_SPECS"],
        ).RUNTIME_SETTING_SPECS)
        assert rows["model"] == '"startup-model"'
        assert rows["channel_context_chars"] == "4321"
        assert rows["runtime_default_location"] == '"Seoul"'
        assert "openai_api_key" not in rows
        assert "discord_token" not in rows
    finally:
        store.close()


def test_runtime_settings_refresh_startup_snapshot_on_restart(tmp_path: Path):
    db = tmp_path / "runtime.sqlite3"
    store = Store(str(db))
    RuntimeSettings(_base(model="first-startup"), store)
    store.close()

    store = Store(str(db))
    try:
        RuntimeSettings(_base(model="second-startup"), store)
        value = store.db.execute(
            "SELECT value FROM runtime_config_startup WHERE key='model'"
        ).fetchone()[0]
        assert value == '"second-startup"'
    finally:
        store.close()

def test_runtime_override_is_immediate_and_survives_reload(tmp_path: Path):
    db = tmp_path / "runtime.sqlite3"
    base = _base(
        model="fixed-startup",
        fast_model="fast-startup",
        smart_model="smart-startup",
        channel_context_chars=5000,
        chat_web_search=True,
        model_routing_smart_threshold=2.0,
        memory_routing_smart_threshold=2.0,
        gemini_thinking_level="low",
        gemini_fast_thinking_level="minimal",
        gemini_smart_thinking_level="medium",
        gemini_store_interactions=False,
    )

    store = Store(str(db))
    settings = RuntimeSettings(base, store)
    assert settings.set_text("LLM_MODEL", "fixed-runtime") == "fixed-runtime"
    assert settings.set_text("LLM_FAST_MODEL", "fast-runtime") == "fast-runtime"
    assert settings.set_text("LLM_SMART_MODEL", "smart-runtime") == "smart-runtime"
    assert settings.set_text("CHANNEL_CONTEXT_CHARS", "8000") == 8000
    assert settings.set_text("chat_web_search", "off") is False
    assert settings.set_text("CALL_PREFIXES", "히나야, 히나") == ("히나야", "히나")
    assert settings.set_text("ALWAYS_REPLY_CHANNEL_IDS", "10, 20, 10") == frozenset({10, 20})
    assert settings.set_text("MODEL_ROUTING_SMART_THRESHOLD", "1.8") == pytest.approx(1.8)
    assert settings.set_text("MEMORY_ROUTING_SMART_THRESHOLD", "2.3") == pytest.approx(2.3)
    assert settings.set_text("GEMINI_THINKING_LEVEL", "HIGH") == "high"
    assert settings.set_text("GEMINI_FAST_THINKING_LEVEL", "low") == "low"
    assert settings.set_text("GEMINI_SMART_THINKING_LEVEL", "high") == "high"
    assert settings.set_text("GEMINI_STORE_INTERACTIONS", "on") is True
    store.close()

    store = Store(str(db))
    try:
        reloaded = RuntimeSettings(base, store)
        assert reloaded.model == "fixed-runtime"
        assert reloaded.fast_model == "fast-runtime"
        assert reloaded.smart_model == "smart-runtime"
        assert reloaded.channel_context_chars == 8000
        assert reloaded.chat_web_search is False
        assert reloaded.call_prefixes == ("히나야", "히나")
        assert reloaded.always_reply_channel_ids == frozenset({10, 20})
        assert reloaded.model_routing_smart_threshold == pytest.approx(1.8)
        assert reloaded.memory_routing_smart_threshold == pytest.approx(2.3)
        assert reloaded.gemini_thinking_level == "high"
        assert reloaded.gemini_fast_thinking_level == "low"
        assert reloaded.gemini_smart_thinking_level == "high"
        assert reloaded.gemini_store_interactions is True
        assert reloaded.source("LLM_MODEL") == "db"
        assert reloaded.source("LLM_FAST_MODEL") == "db"
        assert reloaded.source("LLM_SMART_MODEL") == "db"
        assert reloaded.source("GEMINI_THINKING_LEVEL") == "db"
        assert reloaded.source("GEMINI_FAST_THINKING_LEVEL") == "db"
        assert reloaded.source("GEMINI_SMART_THINKING_LEVEL") == "db"
        assert reloaded.source("GEMINI_STORE_INTERACTIONS") == "db"
        assert reloaded.source("ALWAYS_REPLY_CHANNEL_IDS") == "db"
        assert reloaded.source("CHANNEL_CONTEXT_CHARS") == "db"
        assert reloaded.source("MODEL_ROUTING_SMART_THRESHOLD") == "db"
        assert reloaded.source("MEMORY_ROUTING_SMART_THRESHOLD") == "db"
    finally:
        store.close()


def test_reset_removes_db_override_and_restores_startup_value():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(
            _base(
                model="fixed-startup",
                fast_model="fast-startup",
                smart_model="smart-startup",
                lore_max_items=9,
                model_routing_smart_threshold=2.3,
                memory_routing_smart_threshold=2.4,
            ),
            store,
        )
        settings.set_text("LLM_MODEL", "fixed-runtime")
        settings.set_text("LLM_FAST_MODEL", "fast-runtime")
        settings.set_text("LLM_SMART_MODEL", "smart-runtime")
        settings.set_text("LORE_MAX_ITEMS", "3")
        settings.set_text("MODEL_ROUTING_SMART_THRESHOLD", "1.6")
        settings.set_text("MEMORY_ROUTING_SMART_THRESHOLD", "1.7")
        assert settings.model == "fixed-runtime"
        assert settings.fast_model == "fast-runtime"
        assert settings.smart_model == "smart-runtime"
        assert settings.lore_max_items == 3
        assert settings.model_routing_smart_threshold == pytest.approx(1.6)
        assert settings.memory_routing_smart_threshold == pytest.approx(1.7)
        assert settings.reset("LLM_MODEL") == "fixed-startup"
        assert settings.reset("LLM_FAST_MODEL") == "fast-startup"
        assert settings.reset("LLM_SMART_MODEL") == "smart-startup"
        assert settings.reset("LORE_MAX_ITEMS") == 9
        assert settings.reset("MODEL_ROUTING_SMART_THRESHOLD") == pytest.approx(2.3)
        assert settings.reset("MEMORY_ROUTING_SMART_THRESHOLD") == pytest.approx(2.4)
        assert settings.lore_max_items == 9
        assert settings.model_routing_smart_threshold == pytest.approx(2.3)
        assert settings.memory_routing_smart_threshold == pytest.approx(2.4)
        assert settings.source("LORE_MAX_ITEMS") == "startup"
        assert settings.source("MODEL_ROUTING_SMART_THRESHOLD") == "startup"
        assert settings.source("MEMORY_ROUTING_SMART_THRESHOLD") == "startup"
    finally:
        store.close()


def test_runtime_location_can_explicitly_override_env_value_with_empty_string():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(runtime_default_location="Seoul"), store)
        assert settings.set_text("RUNTIME_DEFAULT_LOCATION", "none") == ""
        assert settings.runtime_default_location == ""
        settings.reset("RUNTIME_DEFAULT_LOCATION")
        assert settings.runtime_default_location == "Seoul"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LLM_MODEL", ""),
        ("LLM_FAST_MODEL", ""),
        ("LLM_SMART_MODEL", ""),
        ("LLM_MODEL", "x" * 201),
        ("MAX_OUTPUT_TOKENS", "127"),
        ("MAX_OUTPUT_TOKENS", "65537"),
        ("MODEL_ROUTING_SMART_THRESHOLD", "0"),
        ("MODEL_ROUTING_SMART_THRESHOLD", "10.1"),
        ("MODEL_ROUTING_SMART_THRESHOLD", "nan"),
        ("MODEL_ROUTING_SMART_THRESHOLD", "not-a-number"),
        ("MEMORY_ROUTING_SMART_THRESHOLD", "0"),
        ("MEMORY_ROUTING_SMART_THRESHOLD", "10.1"),
        ("MEMORY_ROUTING_SMART_THRESHOLD", "nan"),
        ("MEMORY_ROUTING_SMART_THRESHOLD", "not-a-number"),
        ("CHANNEL_CONTEXT_CHARS", "12001"),
        ("HISTORY_MAX_CHARS", "-1"),
        ("LORE_MAX_ITEMS", "21"),
        ("LORE_MAX_CHARS", "12001"),
        ("DM_ALWAYS_REPLY", "maybe"),
        ("ALWAYS_REPLY_CHANNEL_IDS", "abc"),
        ("ALWAYS_REPLY_CHANNEL_IDS", "0"),
        ("CALL_PREFIXES", ""),
        ("GEMINI_THINKING_LEVEL", "extreme"),
        ("GEMINI_FAST_THINKING_LEVEL", "off"),
        ("GEMINI_SMART_THINKING_LEVEL", "max"),
        ("GEMINI_STORE_INTERACTIONS", "maybe"),
    ],
)
def test_runtime_setting_validation_rejects_invalid_values(key: str, value: str):
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(), store)
        with pytest.raises(ValueError):
            settings.set_text(key, value)
    finally:
        store.close()
