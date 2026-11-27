import json
import re
from pathlib import Path

import pytest

from hina_bot.core.config import Settings, parse_openrouter_preferences
from hina_bot.core.runtime_config import (
    RUNTIME_SETTING_SPECS,
    RuntimeSettings,
    decode_runtime_value,
    parse_runtime_value,
)
from hina_bot.core.store import Store


def _base(**overrides):
    values = {
        "openai_api_key": "key",
        "discord_token": "token",
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_load_preserves_trailing_space_in_call_prefix(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("CALL_PREFIXES", "히나 ")
    assert Settings.load().call_prefixes == ("히나 ",)


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
        "ANSWER_TIMEOUT_SECONDS",
        "DECISION_TIMEOUT_SECONDS",
        "ROUTING_CLASSIFIER_MODE",
        "ROUTING_CLASSIFIER_PROVIDER",
        "ROUTING_CLASSIFIER_MODEL",
        "ROUTING_CLASSIFIER_API_KEY",
        "ROUTING_CLASSIFIER_TIMEOUT_SECONDS",
        "ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS",
        "OUTPUT_REPAIR_MODE",
        "OUTPUT_REPAIR_MODEL",
        "OUTPUT_REPAIR_TIMEOUT_SECONDS",
        "GEMINI_THINKING_LEVEL",
        "GEMINI_FAST_THINKING_LEVEL",
        "GEMINI_SMART_THINKING_LEVEL",
        "GEMINI_STORE_INTERACTIONS",
        "GEMINI_STORE_CLASSIFIER_INTERACTIONS",
        "GEMINI_SEARCH_API_MODE",
        "CODE_EXECUTION_BACKEND",
        "CHANNEL_CONTEXT_CHARS",
        "RECENT_CONTEXT_WINDOW_MINUTES",
        "CONVERSATION_SESSION_GAP_MINUTES",
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
    assert settings.answer_timeout_seconds == pytest.approx(45.0)
    assert settings.decision_timeout_seconds == pytest.approx(1.5)
    assert settings.gemini_store_interactions is False
    assert settings.gemini_store_classifier_interactions is None
    assert settings.gemini_search_api_mode == "auto"
    assert settings.code_execution_backend == "auto"
    assert settings.routing_classifier_mode == "off"
    assert settings.routing_classifier_provider == "openai"
    assert settings.routing_classifier_model == settings.fast_model
    assert settings.routing_classifier_timeout_seconds == pytest.approx(4.0)
    assert settings.routing_classifier_max_output_tokens == 256
    assert settings.output_repair_mode == "off"
    assert settings.output_repair_model == "auto"
    assert settings.output_repair_timeout_seconds == pytest.approx(3.0)
    assert settings.channel_context_chars == 6000
    assert settings.recent_context_window_minutes == 60
    assert settings.conversation_session_gap_minutes == 240
    assert settings.history_max_chars == 12000
    assert settings.lore_max_items == 6
    assert settings.lore_max_chars == 3200
    assert settings.runtime_default_location == ""
    assert settings.event_log_path == "data/logs/events.jsonl"




def test_recent_context_window_env_and_runtime_bounds(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("RECENT_CONTEXT_WINDOW_MINUTES", "90")
    base = Settings.load()
    assert base.recent_context_window_minutes == 90

    store = Store.create(":memory:")
    try:
        runtime = RuntimeSettings(base, store)
        assert runtime.set_text("RECENT_CONTEXT_WINDOW_MINUTES", "180") == 180
        assert runtime.recent_context_window_minutes == 180
        for invalid in ("14", "181", "nan", "90.5"):
            with pytest.raises(ValueError):
                runtime.set_text("recent_context_window_minutes", invalid)
        assert runtime.recent_context_window_minutes == 180
        assert runtime.reset("RECENT_CONTEXT_WINDOW_MINUTES") == 90
        assert runtime.recent_context_window_minutes == 90
    finally:
        store.close()


@pytest.mark.parametrize("raw", ["14", "181", "not-a-number"])
def test_recent_context_window_rejects_invalid_env(monkeypatch, tmp_path: Path, raw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("RECENT_CONTEXT_WINDOW_MINUTES", raw)
    with pytest.raises(ValueError):
        Settings.load()


def test_conversation_session_gap_env_and_runtime_override(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("CONVERSATION_SESSION_GAP_MINUTES", "90")
    base = Settings.load()
    assert base.conversation_session_gap_minutes == 90

    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(base, store)
        assert settings.set_text("CONVERSATION_SESSION_GAP_MINUTES", "1440") == 1440
        assert settings.conversation_session_gap_minutes == 1440
        assert settings.set_text("CONVERSATION_SESSION_GAP_MINUTES", "0") == 0
        assert settings.conversation_session_gap_minutes == 0
        for invalid in ("-1", "1", "14", "1441", "nan", "60.5"):
            with pytest.raises(ValueError):
                settings.set_text("conversation_session_gap_minutes", invalid)
        assert settings.conversation_session_gap_minutes == 0
        assert settings.reset("CONVERSATION_SESSION_GAP_MINUTES") == 90
        assert settings.conversation_session_gap_minutes == 90
    finally:
        store.close()


@pytest.mark.parametrize("raw", ["-1", "1", "14", "1441", "not-a-number"])
def test_conversation_session_gap_rejects_invalid_env(monkeypatch, tmp_path: Path, raw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("CONVERSATION_SESSION_GAP_MINUTES", raw)
    with pytest.raises(ValueError):
        Settings.load()


def test_conversation_session_gap_env_accepts_unlimited(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("CONVERSATION_SESSION_GAP_MINUTES", "0")
    assert Settings.load().conversation_session_gap_minutes == 0


def test_env_example_lists_all_runtime_setting_names():
    template = (Path(__file__).resolve().parents[2] / ".env.example").read_text(
        encoding="utf-8"
    )
    listed = set(re.findall(r"(?m)^\s*#?\s*([A-Z][A-Z0-9_]*)=", template))
    missing = {spec.env_name for spec in RUNTIME_SETTING_SPECS.values()} - listed
    assert not missing, f"Missing runtime environment variables in .env.example: {sorted(missing)}"


def test_answer_timeout_env_runtime_override_and_reset(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("ANSWER_TIMEOUT_SECONDS", "75.5")
    base = Settings.load()
    assert base.answer_timeout_seconds == pytest.approx(75.5)

    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(base, store)
        assert settings.set_text("ANSWER_TIMEOUT_SECONDS", "120.25") == pytest.approx(120.25)
        assert settings.answer_timeout_seconds == pytest.approx(120.25)
        assert settings.source("answer_timeout_seconds") == "db"
        for invalid in ("0.99", "300.01", "nan", "inf", "bad"):
            with pytest.raises(ValueError):
                settings.set_text("ANSWER_TIMEOUT_SECONDS", invalid)
        assert settings.answer_timeout_seconds == pytest.approx(120.25)
        assert settings.reset("ANSWER_TIMEOUT_SECONDS") == pytest.approx(75.5)
        assert settings.source("answer_timeout_seconds") == "startup"
    finally:
        store.close()


@pytest.mark.parametrize("raw", ["0", "0.99", "300.01", "nan", "inf", "invalid"])
def test_settings_rejects_invalid_answer_timeout_env(monkeypatch, tmp_path: Path, raw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("ANSWER_TIMEOUT_SECONDS", raw)
    with pytest.raises(ValueError, match="ANSWER_TIMEOUT_SECONDS"):
        Settings.load()


@pytest.mark.parametrize("raw", ["1", "300"])
def test_settings_accepts_answer_timeout_bounds(monkeypatch, tmp_path: Path, raw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("ANSWER_TIMEOUT_SECONDS", raw)
    assert Settings.load().answer_timeout_seconds == pytest.approx(float(raw))


def test_decision_timeout_env_runtime_override_and_reset(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DECISION_TIMEOUT_SECONDS", "2.5")
    base = Settings.load()
    assert base.decision_timeout_seconds == pytest.approx(2.5)

    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(base, store)
        assert settings.set_text("DECISION_TIMEOUT_SECONDS", "0.75") == pytest.approx(0.75)
        assert settings.decision_timeout_seconds == pytest.approx(0.75)
        assert settings.source("decision_timeout_seconds") == "db"
        for invalid in ("0", "0.24", "30.01", "nan", "inf", "bad"):
            with pytest.raises(ValueError):
                settings.set_text("DECISION_TIMEOUT_SECONDS", invalid)
        settings.reload()
        assert settings.decision_timeout_seconds == pytest.approx(0.75)
        assert settings.reset("DECISION_TIMEOUT_SECONDS") == pytest.approx(2.5)
        assert settings.source("decision_timeout_seconds") == "startup"
    finally:
        store.close()


@pytest.mark.parametrize("raw", ["0", "0.24", "30.01", "nan", "inf", "invalid"])
def test_settings_rejects_invalid_decision_timeout_env(monkeypatch, tmp_path: Path, raw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DECISION_TIMEOUT_SECONDS", raw)
    with pytest.raises(ValueError, match="DECISION_TIMEOUT_SECONDS"):
        Settings.load()


@pytest.mark.parametrize("raw", ["0.25", "30"])
def test_settings_accepts_decision_timeout_bounds(monkeypatch, tmp_path: Path, raw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("DECISION_TIMEOUT_SECONDS", raw)
    assert Settings.load().decision_timeout_seconds == pytest.approx(float(raw))


def test_settings_loads_output_repair_from_dotenv_and_runtime_reset(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    for name in ("DISCORD_TOKEN", "OPENAI_API_KEY", "OUTPUT_REPAIR_MODE",
                 "OUTPUT_REPAIR_MODEL", "OUTPUT_REPAIR_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "DISCORD_TOKEN=token\n"
        "OPENAI_API_KEY=key\n"
        "OUTPUT_REPAIR_MODE=conservative\n"
        "OUTPUT_REPAIR_MODEL=repair-model\n"
        "OUTPUT_REPAIR_TIMEOUT_SECONDS=2.5\n",
        encoding="utf-8",
    )

    base = Settings.load()
    assert base.output_repair_mode == "conservative"
    assert base.output_repair_model == "repair-model"
    assert base.output_repair_timeout_seconds == pytest.approx(2.5)

    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(base, store)
        assert settings.set_text("OUTPUT_REPAIR_MODE", "aggressive") == "aggressive"
        assert settings.set_text("OUTPUT_REPAIR_MODEL", "db-model") == "db-model"
        assert settings.set_text("OUTPUT_REPAIR_TIMEOUT_SECONDS", "0.5") == 0.5
        assert settings.output_repair_mode == "aggressive"
        assert settings.output_repair_model == "db-model"
        assert settings.output_repair_timeout_seconds == pytest.approx(0.5)
        assert settings.reset("OUTPUT_REPAIR_MODE") == "conservative"
        assert settings.reset("OUTPUT_REPAIR_MODEL") == "repair-model"
        assert settings.reset("OUTPUT_REPAIR_TIMEOUT_SECONDS") == pytest.approx(2.5)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("OUTPUT_REPAIR_MODE", "invalid"),
        ("OUTPUT_REPAIR_MODE", ""),
        ("OUTPUT_REPAIR_MODE", "shadow"),
        ("OUTPUT_REPAIR_MODE", "active"),
        ("OUTPUT_REPAIR_MODEL", ""),
        ("OUTPUT_REPAIR_MODEL", "x" * 201),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "invalid"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "nan"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "inf"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "0.24"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "30.01"),
    ],
)
def test_settings_rejects_invalid_output_repair_env(monkeypatch, tmp_path: Path, name, raw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError, match=name):
        Settings.load()


@pytest.mark.parametrize("seconds", ["0.25", "30.0"])
def test_settings_accepts_output_repair_timeout_bounds(monkeypatch, tmp_path: Path, seconds):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("OUTPUT_REPAIR_TIMEOUT_SECONDS", seconds)
    assert Settings.load().output_repair_timeout_seconds == pytest.approx(float(seconds))


def test_settings_rejects_invalid_gemini_search_api_mode(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("GEMINI_SEARCH_API_MODE", "unknown")
    with pytest.raises(ValueError, match="GEMINI_SEARCH_API_MODE"):
        Settings.load()


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
    monkeypatch.setenv("GEMINI_STORE_CLASSIFIER_INTERACTIONS", "true")
    monkeypatch.setenv("GEMINI_SEARCH_API_MODE", "interactions")

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
    assert value.gemini_store_classifier_interactions is True
    assert value.gemini_search_api_mode == "interactions"
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
    store = Store.create(":memory:")
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
        assert settings.gemini_store_classifier_interactions is None
        assert settings.gemini_search_api_mode == "auto"
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
    store = Store.create(str(db))
    try:
        RuntimeSettings(
            _base(
                model="startup-model",
                channel_context_chars=4321,
                runtime_default_location="Seoul",
            ),
            store,
        ).capture_startup_snapshot()
        rows = {
            row["key"]: row["value"]
            for row in store.db.execute(
                "SELECT key,value FROM runtime_config_startup"
            ).fetchall()
        }
        assert set(rows) == set(RUNTIME_SETTING_SPECS)
        assert rows["model"] == '"startup-model"'
        assert rows["channel_context_chars"] == "4321"
        assert rows["runtime_default_location"] == '"Seoul"'
        assert "openai_api_key" not in rows
        assert "discord_token" not in rows
    finally:
        store.close()


def test_runtime_settings_refresh_startup_snapshot_on_restart(tmp_path: Path):
    db = tmp_path / "runtime.sqlite3"
    store = Store.create(str(db))
    RuntimeSettings(_base(model="first-startup"), store).capture_startup_snapshot()
    store.close()

    store = Store.create(str(db))
    try:
        RuntimeSettings(_base(model="second-startup"), store).capture_startup_snapshot()
        value = store.db.execute(
            "SELECT value FROM runtime_config_startup WHERE key='model'"
        ).fetchone()[0]
        assert value == '"second-startup"'
    finally:
        store.close()

def test_runtime_call_prefix_preserves_trailing_space_after_reload(tmp_path: Path):
    db = tmp_path / "runtime-prefix.sqlite3"
    store = Store.create(str(db))
    try:
        runtime = RuntimeSettings(_base(), store)
        assert runtime.set_text("CALL_PREFIXES", "히나 ") == ("히나 ",)
        assert runtime.call_prefixes == ("히나 ",)
        runtime.reload()
        assert runtime.call_prefixes == ("히나 ",)
    finally:
        store.close()

    store = Store.create(str(db))
    try:
        reloaded = RuntimeSettings(_base(), store)
        assert reloaded.call_prefixes == ("히나 ",)
        assert reloaded.source("CALL_PREFIXES") == "db"
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
        gemini_store_classifier_interactions=False,
    )

    store = Store.create(str(db))
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
    assert settings.set_text("GEMINI_STORE_CLASSIFIER_INTERACTIONS", "on") is True
    assert settings.set_text("GEMINI_SEARCH_API_MODE", "Generate_Content") == "generate_content"
    store.close()

    store = Store.create(str(db))
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
        assert reloaded.gemini_store_classifier_interactions is True
        assert reloaded.gemini_search_api_mode == "generate_content"
        assert reloaded.source("LLM_MODEL") == "db"
        assert reloaded.source("LLM_FAST_MODEL") == "db"
        assert reloaded.source("LLM_SMART_MODEL") == "db"
        assert reloaded.source("GEMINI_THINKING_LEVEL") == "db"
        assert reloaded.source("GEMINI_FAST_THINKING_LEVEL") == "db"
        assert reloaded.source("GEMINI_SMART_THINKING_LEVEL") == "db"
        assert reloaded.source("GEMINI_STORE_INTERACTIONS") == "db"
        assert reloaded.source("GEMINI_STORE_CLASSIFIER_INTERACTIONS") == "db"
        assert reloaded.source("GEMINI_SEARCH_API_MODE") == "db"
        assert reloaded.source("ALWAYS_REPLY_CHANNEL_IDS") == "db"
        assert reloaded.source("CHANNEL_CONTEXT_CHARS") == "db"
        assert reloaded.source("MODEL_ROUTING_SMART_THRESHOLD") == "db"
        assert reloaded.source("MEMORY_ROUTING_SMART_THRESHOLD") == "db"
    finally:
        store.close()


def test_expanded_runtime_settings_apply_immediately_and_survive_reload(tmp_path: Path):
    db = tmp_path / "runtime-expanded.sqlite3"
    base = _base(
        model_routing_mode="fixed",
        fast_output_tokens=4096,
        smart_output_tokens=8192,
        memory_output_tokens=4096,
        routing_classifier_max_output_tokens=256,
        output_repair_timeout_seconds=3.0,
        cooldown=5,
        summary_every=8,
        structured_memory_every=4,
        structured_memory_stale_after_seconds=28800,
        runtime_timezone="Asia/Seoul",
        runtime_locale="ko-KR",
        empty_call_reply="응?",
        special_dm_empty_call_reply="선생님?",
        empty_response_reply="응.",
    )

    store = Store.create(str(db))
    settings = RuntimeSettings(base, store)
    assert settings.set_text("MODEL_ROUTING_MODE", "adaptive") == "adaptive"
    assert settings.set_text("FAST_MAX_OUTPUT_TOKENS", "6000") == 6000
    assert settings.set_text("SMART_MAX_OUTPUT_TOKENS", "9000") == 9000
    assert settings.set_text("MEMORY_MAX_OUTPUT_TOKENS", "5000") == 5000
    assert settings.set_text("ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS", "384") == 384
    assert settings.set_text("OUTPUT_REPAIR_TIMEOUT_SECONDS", "6.5") == pytest.approx(6.5)
    assert settings.set_text("COOLDOWN_SECONDS", "1.5") == pytest.approx(1.5)
    assert settings.set_text("SUMMARY_EVERY", "6") == 6
    assert settings.set_text("STRUCTURED_MEMORY_EVERY", "3") == 3
    assert settings.set_text("STRUCTURED_MEMORY_STALE_AFTER_SECONDS", "7200") == 7200
    assert settings.set_text("RUNTIME_TIMEZONE", "UTC") == "UTC"
    assert settings.set_text("RUNTIME_LOCALE", "en-US") == "en-US"
    assert settings.set_text("EMPTY_CALL_REPLY", "hey") == "hey"
    assert settings.set_text("SPECIAL_DM_EMPTY_CALL_REPLY", "none") == ""
    assert settings.set_text("EMPTY_RESPONSE_REPLY", "fallback") == "fallback"
    store.close()

    store = Store.create(str(db))
    try:
        reloaded = RuntimeSettings(base, store)
        assert reloaded.model_routing_mode == "adaptive"
        assert reloaded.fast_output_tokens == 6000
        assert reloaded.smart_output_tokens == 9000
        assert reloaded.memory_output_tokens == 5000
        assert reloaded.routing_classifier_max_output_tokens == 384
        assert reloaded.output_repair_timeout_seconds == pytest.approx(6.5)
        assert reloaded.cooldown == pytest.approx(1.5)
        assert reloaded.summary_every == 6
        assert reloaded.structured_memory_every == 3
        assert reloaded.structured_memory_stale_after_seconds == 7200
        assert reloaded.runtime_timezone == "UTC"
        assert reloaded.runtime_locale == "en-US"
        assert reloaded.empty_call_reply == "hey"
        assert reloaded.special_dm_empty_call_reply == ""
        assert reloaded.empty_response_reply == "fallback"
        assert reloaded.source("FAST_MAX_OUTPUT_TOKENS") == "db"
        assert reloaded.source("RUNTIME_TIMEZONE") == "db"
        assert reloaded.source("OUTPUT_REPAIR_TIMEOUT_SECONDS") == "db"
    finally:
        store.close()


def test_runtime_cross_field_constraints_match_startup_invariants():
    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(
            _base(
                fast_output_tokens=4096,
                smart_output_tokens=8192,
                history_turns=12,
            ),
            store,
        )
        with pytest.raises(ValueError, match="FAST_MAX_OUTPUT_TOKENS"):
            settings.set_text("FAST_MAX_OUTPUT_TOKENS", "9000")
        with pytest.raises(ValueError, match="SMART_MAX_OUTPUT_TOKENS"):
            settings.set_text("SMART_MAX_OUTPUT_TOKENS", "4000")
        with pytest.raises(ValueError, match="HISTORY_TURNS"):
            settings.set_text("SUMMARY_EVERY", "13")
        with pytest.raises(ValueError, match="HISTORY_TURNS"):
            settings.set_text("STRUCTURED_MEMORY_EVERY", "13")
    finally:
        store.close()


def test_reset_removes_db_override_and_restores_startup_value():
    store = Store.create(":memory:")
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
    store = Store.create(":memory:")
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
        ("MODEL_ROUTING_MODE", "dynamic"),
        ("FAST_MAX_OUTPUT_TOKENS", "127"),
        ("SMART_MAX_OUTPUT_TOKENS", "65537"),
        ("MEMORY_MAX_OUTPUT_TOKENS", "127"),
        ("ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS", "31"),
        ("ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS", "1025"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "0.24"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "30.01"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "nan"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "inf"),
        ("OUTPUT_REPAIR_TIMEOUT_SECONDS", "invalid"),
        ("OUTPUT_REPAIR_MODE", "shadow"),
        ("OUTPUT_REPAIR_MODE", "active"),
        ("COOLDOWN_SECONDS", "-1"),
        ("SUMMARY_EVERY", "1"),
        ("STRUCTURED_MEMORY_EVERY", "31"),
        ("STRUCTURED_MEMORY_STALE_AFTER_SECONDS", "59"),
        ("RUNTIME_TIMEZONE", "Mars/Olympus"),
        ("RUNTIME_LOCALE", "x" * 33),
        ("EMPTY_CALL_REPLY", ""),
        ("EMPTY_RESPONSE_REPLY", ""),
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
        ("GEMINI_STORE_CLASSIFIER_INTERACTIONS", "maybe"),
        ("GEMINI_SEARCH_API_MODE", "legacy"),
    ],
)
def test_runtime_setting_validation_rejects_invalid_values(key: str, value: str):
    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(_base(), store)
        with pytest.raises(ValueError):
            settings.set_text(key, value)
    finally:
        store.close()



def test_runtime_setting_specs_have_operator_descriptions():
    assert RUNTIME_SETTING_SPECS
    assert all(spec.description.strip() for spec in RUNTIME_SETTING_SPECS.values())


def test_runtime_collection_specs_expose_editor_constraints():
    prefixes = RUNTIME_SETTING_SPECS["call_prefixes"]
    channels = RUNTIME_SETTING_SPECS["always_reply_channel_ids"]

    assert prefixes.kind == "prefixes"
    assert prefixes.maximum == 20
    assert prefixes.empty_allowed is False

    assert channels.kind == "discord_ids"
    assert channels.maximum == 100
    assert channels.empty_allowed is True



def test_runtime_scalar_specs_expose_editor_constraints():
    external = RUNTIME_SETTING_SPECS["external_context_policy"]
    thinking = RUNTIME_SETTING_SPECS["gemini_thinking_level"]
    output_tokens = RUNTIME_SETTING_SPECS["output_tokens"]
    threshold = RUNTIME_SETTING_SPECS["model_routing_smart_threshold"]
    model = RUNTIME_SETTING_SPECS["model"]
    routing_mode = RUNTIME_SETTING_SPECS["model_routing_mode"]
    classifier_budget = RUNTIME_SETTING_SPECS["routing_classifier_max_output_tokens"]
    stale = RUNTIME_SETTING_SPECS["structured_memory_stale_after_seconds"]

    assert set(external.choices) == {"full", "bot_interactions_only"}
    assert set(thinking.choices) == {"minimal", "low", "medium", "high"}
    assert output_tokens.minimum == 128
    assert output_tokens.maximum == 65536
    assert threshold.minimum == pytest.approx(0.1)
    assert threshold.maximum == pytest.approx(10.0)
    assert model.maximum == 200
    assert set(routing_mode.choices) == {"fixed", "adaptive"}
    assert classifier_budget.minimum == 32
    assert classifier_budget.maximum == 1024
    assert stale.minimum == 60
    assert stale.maximum == 7 * 24 * 60 * 60

def test_rag_mode_defaults_to_full_and_accepts_tool_mode():
    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(_base(), store)
        assert settings.rag_mode == "full"
        assert set(RUNTIME_SETTING_SPECS["rag_mode"].choices) == {
            "off", "full", "tool", "decision",
        }
        assert settings.set_text("RAG_MODE", "tool") == "tool"
        for removed in ("shadow", "v2", "v1", "decision_shadow"):
            with pytest.raises(ValueError):
                settings.set_text("RAG_MODE", removed)
        assert settings.rag_mode == "tool"
        # The old alias and the v2-only knobs are no longer runtime settings (#463).
        for key in ("RETRIEVAL_V2_MODE", "RETRIEVAL_V2_TIMEOUT_SECONDS"):
            with pytest.raises(ValueError):
                settings.set_text(key, "off")
    finally:
        store.close()


@pytest.mark.parametrize(("key", "old"), [
    ("rag_mode", "v1"), ("rag_mode", "decision_shadow"),
    ("personal_memory_read_mode", "decision_shadow"),
])
def test_retired_modes_migrate_to_full(key, old, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv(key.upper(), old)
    assert getattr(Settings.load(), key) == "full"
    store = Store.create(":memory:")
    try:
        encoded = f'"{old}"'
        store.settings.set_runtime_value(key, encoded)
        store.settings.replace_startup_snapshot([(key, encoded)])
        settings = RuntimeSettings(_base(), store)
        assert getattr(settings, key) == "full"
        assert settings.source(key) == "db"
        with pytest.raises(ValueError):
            settings.set_text(key, old)
        store.settings.migrate_memory_read_mode()
        assert store.settings.runtime_values()[key] == '"full"'
        assert store.db.execute(
            "SELECT value FROM runtime_config_startup WHERE key=?", (key,)
        ).fetchone()[0] == '"full"'
    finally:
        store.close()


def test_stored_removed_rag_values_fall_back_to_startup():
    store = Store.create(":memory:")
    try:
        store.settings.set_runtime_value("rag_mode", '"v2"')
        store.settings.set_runtime_value("retrieval_v2_timeout_seconds", "2.5")
        settings = RuntimeSettings(_base(), store)
        assert settings.rag_mode == "full"
        assert settings.source("rag_mode") == "startup"
    finally:
        store.close()


def test_settings_load_rag_mode_defaults_to_full_and_ignores_old_alias(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "answer-key")
    monkeypatch.delenv("RAG_MODE", raising=False)
    monkeypatch.setenv("RETRIEVAL_V2_MODE", "active")
    assert Settings.load().rag_mode == "full"

    monkeypatch.setenv("RAG_MODE", "v1")
    assert Settings.load().rag_mode == "full"
    monkeypatch.setenv("RAG_MODE", "tool")
    assert Settings.load().rag_mode == "tool"

    for removed in ("shadow", "v2"):
        monkeypatch.setenv("RAG_MODE", removed)
        with pytest.raises(ValueError, match="RAG_MODE"):
            Settings.load()



def test_old_memory_shadow_config_is_read_and_migrated_without_changing_other_settings():
    store = Store.create(":memory:")
    try:
        store.settings.set_runtime_value("personal_memory_read_mode", '"shadow"')
        store.settings.set_runtime_value("output_repair_mode", '"shadow"')
        store.settings.replace_startup_snapshot([("personal_memory_read_mode", '"shadow"')])
        settings = RuntimeSettings(_base(), store)
        assert settings.personal_memory_read_mode == "full"
        assert settings.source("PERSONAL_MEMORY_READ_MODE") == "db"
        with pytest.raises(ValueError, match="full"):
            settings.set_text("PERSONAL_MEMORY_READ_MODE", "shadow")

        for _ in range(2):
            store.settings.migrate_memory_read_mode()
        assert store.settings.runtime_values() == {
            "personal_memory_read_mode": '"full"', "output_repair_mode": '"shadow"',
        }
        assert store.db.execute(
            "SELECT value FROM runtime_config_startup WHERE key='personal_memory_read_mode'"
        ).fetchone()[0] == '"full"'
        for mode in ("full", "decision"):
            assert settings.set_text("PERSONAL_MEMORY_READ_MODE", mode) == mode
            assert settings.set_text("RAG_MODE", mode) == mode
    finally:
        store.close()


def test_legacy_memory_shadow_environment_value_is_canonical(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("PERSONAL_MEMORY_READ_MODE", "shadow")
    assert Settings.load().personal_memory_read_mode == "full"


def test_manual_full_mode_persists_and_reads_legacy_overrides(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("PERSONAL_MEMORY_READ_MODE", "legacy")
    assert Settings.load().personal_memory_read_mode == "full"

    store = Store.create(":memory:")
    try:
        store.settings.set_runtime_value("personal_memory_read_mode", '"legacy"')
        settings = RuntimeSettings(_base(), store)
        assert settings.personal_memory_read_mode == "full"
        for mode in ("decision", "full"):
            assert settings.set_text("PERSONAL_MEMORY_READ_MODE", mode) == mode
        assert RuntimeSettings(_base(), store).personal_memory_read_mode == "full"
    finally:
        store.close()


def test_cross_scope_default_can_change_without_creating_opt_ins(tmp_path):
    path = str(tmp_path / "preferences.db")
    store = Store.create(path)
    try:
        settings = RuntimeSettings(_base(), store)
        assert settings.personal_memory_cross_scope == "on"
        store.set_cross_scope_opt_in(100, True)
        assert settings.set_text("PERSONAL_MEMORY_CROSS_SCOPE", "off") == "off"
    finally:
        store.close()
    store = Store.create(path)
    try:
        assert RuntimeSettings(_base(), store).personal_memory_cross_scope == "off"
        assert store.cross_scope_opt_in(100)
        assert not store.cross_scope_opt_in(200)
    finally:
        store.close()


def test_vision_transfer_budget_environment_and_live_overrides(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("VISION_MAX_IMAGE_MB", "8")
    monkeypatch.setenv("VISION_MAX_TOTAL_MB", "16")
    base = Settings.load()
    assert (base.vision_max_image_mb, base.vision_max_total_mb) == (8, 16)

    store = Store.create(":memory:")
    try:
        runtime = RuntimeSettings(base, store)
        runtime.set_text("VISION_MAX_IMAGE_MB", "6")
        runtime.set_text("VISION_MAX_TOTAL_MB", "10")
        from hina_bot.discord.vision import VisionLimits

        limits = VisionLimits.from_settings(runtime)
        assert limits.max_image_bytes == 6 * 1024 * 1024
        assert limits.max_total_bytes == 10 * 1024 * 1024
        with pytest.raises(ValueError):
            runtime.set_text("VISION_MAX_IMAGE_MB", "21")
        with pytest.raises(ValueError):
            runtime.set_text("VISION_MAX_TOTAL_MB", "0")
        runtime.reload()
        assert runtime.vision_max_image_mb == 6
    finally:
        store.close()

    monkeypatch.setenv("VISION_MAX_IMAGE_MB", "21")
    with pytest.raises(ValueError):
        Settings.load()


def test_retired_followup_removed_and_all_legacy_writes_rejected():
    store = Store.create(":memory:")
    try:
        store.settings.set_runtime_value("memory_session_followup", '"dm"')
        store.settings.replace_startup_snapshot([("memory_session_followup", '"all"')])
        store.settings.migrate_memory_read_mode()
        assert "memory_session_followup" not in store.settings.runtime_values()
        assert not store.db.execute(
            "SELECT 1 FROM runtime_config_startup WHERE key='memory_session_followup'"
        ).fetchone()
        settings = RuntimeSettings(_base(), store)
        for key, values in (
            ("PERSONAL_MEMORY_READ_MODE", ("legacy", "shadow", "tiered", "tiered_shadow", "decision_shadow")),
            ("RAG_MODE", ("v1", "decision_shadow")),
            ("MEMORY_SESSION_FOLLOWUP", ("dm",)),
        ):
            for value in values:
                with pytest.raises(ValueError):
                    settings.set_text(key, value)
    finally:
        store.close()


def test_code_execution_backend_env_runtime_override_and_reset(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("CODE_EXECUTION_BACKEND", "Native")
    base = Settings.load()
    assert base.code_execution_backend == "native"
    assert set(RUNTIME_SETTING_SPECS["code_execution_backend"].choices) == {
        "auto", "native", "delegated",
    }
    store = Store.create(":memory:")
    try:
        settings = RuntimeSettings(base, store)
        assert settings.set_text("CODE_EXECUTION_BACKEND", "Delegated") == "delegated"
        assert settings.code_execution_backend == "delegated"
        settings.reload()
        assert settings.code_execution_backend == "delegated"
        for invalid in ("", "local", "unknown"):
            with pytest.raises(ValueError):
                settings.set_text("CODE_EXECUTION_BACKEND", invalid)
        assert settings.reset("CODE_EXECUTION_BACKEND") == "native"
        assert settings.code_execution_backend == "native"
    finally:
        store.close()


@pytest.mark.parametrize("invalid", ["", "local", "unknown"])
def test_code_execution_backend_rejects_invalid_env(monkeypatch, tmp_path: Path, invalid):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("CODE_EXECUTION_BACKEND", invalid)
    with pytest.raises(ValueError, match="CODE_EXECUTION_BACKEND"):
        Settings.load()


def test_pdf_scope_and_legacy_transport_migration(monkeypatch, tmp_path):
    from hina_bot.core.config import migrate_legacy_env_values

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("GEMINI_PDF_TRANSPORT", "AUTO")
    monkeypatch.setenv("GEMINI_PDF_SCOPE", "Memory, answer, memory")
    base = Settings.load()
    assert base.gemini_pdf_transport == "text"
    assert base.gemini_pdf_scope == "answer,memory"
    assert migrate_legacy_env_values({"GEMINI_PDF_TRANSPORT": "auto",
                                      "GEMINI_PDF_SCOPE": "memory,answer"}) == {
        "GEMINI_PDF_TRANSPORT": "text", "GEMINI_PDF_SCOPE": "answer,memory",
    }
    store = Store.create(":memory:")
    try:
        store.settings.set_runtime_value("gemini_pdf_transport", '"auto"')
        store.settings.set_runtime_value("gemini_pdf_scope", '"knowledge, answer"')
        runtime = RuntimeSettings(base, store)
        assert runtime.gemini_pdf_transport == "text"
        assert runtime.gemini_pdf_scope == "answer,knowledge"
        assert store.settings.runtime_values()["gemini_pdf_transport"] == '"text"'
        assert store.settings.runtime_values()["gemini_pdf_scope"] == '"answer,knowledge"'
        assert runtime.set_text("GEMINI_PDF_SCOPE", "answer,memory,decisions,knowledge") == "all"
        assert runtime.set_text("GEMINI_PDF_TRANSPORT", "PDF") == "pdf"
        runtime.reload()
        assert runtime.gemini_pdf_transport == "pdf"
        for value in ("all,answer", "answer,,memory", "bogus", ""):
            with pytest.raises(ValueError):
                runtime.set_text("GEMINI_PDF_SCOPE", value)
        with pytest.raises(ValueError):
            runtime.set_text("GEMINI_PDF_TRANSPORT", "auto")
        assert runtime.reset("GEMINI_PDF_TRANSPORT") == "text"
        assert runtime.reset("GEMINI_PDF_SCOPE") == "answer,memory"
    finally:
        store.close()


@pytest.mark.parametrize("preferences", [
    {"sort": "latency", "ignore": ["slow"], "preferred_max_latency": 3.5},
    {"sort": {"by": "price", "partition": "none"},
     "preferred_max_latency": {"p90": 3, "p99": 8},
     "preferred_min_throughput": {"p50": 100, "p90": 50},
     "max_price": {"prompt": 0.1, "completion": 0.6, "request": 0}},
    {"sort": "throughput", "preferred_min_throughput": 40},
    {"sort": {"by": "latency"}, "zdr": True, "data_collection": "deny"},
    {},
])
def test_openrouter_preferences_valid_and_runtime_roundtrip(preferences):
    raw = json.dumps(preferences)
    spec = RUNTIME_SETTING_SPECS["openrouter_provider_preferences"]
    assert parse_openrouter_preferences(raw) == preferences
    assert parse_runtime_value(spec, raw) == preferences
    assert decode_runtime_value(spec, raw) == preferences


@pytest.mark.parametrize("preferences", [
    {"sort": "fastest"}, {"sort": {"partition": "none"}},
    {"sort": {"by": "price", "partition": "invalid"}},
    {"sort": {"by": "price", "extra": True}}, {"ignore": "slow"},
    {"preferred_max_latency": {"p95": 3}}, {"preferred_max_latency": {}},
    {"preferred_max_latency": True}, {"preferred_max_latency": -1},
    {"preferred_max_latency": float("inf")},
    {"preferred_min_throughput": {"p90": float("nan")}},
    {"preferred_min_throughput": {"p50": "50"}},
    {"preferred_min_throughput": 0},
    {"max_price": {"prompt": True}}, {"max_price": {"completion": -1}},
    {"max_price": {"unknown": 1}}, {"max_price": {}},
    {"max_price": {"prompt": float("inf")}}, {"unexpected": True},
])
def test_openrouter_preferences_reject_invalid_values(preferences):
    with pytest.raises(ValueError, match="Invalid"):
        parse_openrouter_preferences(json.dumps(preferences))


def test_reset_all_runtime_overrides_preserves_baseline_and_other_data():
    store = Store.create(":memory:")
    try:
        runtime = RuntimeSettings(_base(channel_context_chars=6000), store)
        runtime.capture_startup_snapshot()
        store.settings.set_note("unrelated", "keep")
        runtime.set_text("channel_context_chars", "4300")
        runtime.set_text("recent_context_window_minutes", "90")
        store.settings.set_runtime_value("obsolete", "invalid")
        assert runtime.reset_all() == 3
        assert runtime.channel_context_chars == 6000
        assert runtime.source("recent_context_window_minutes") == "startup"
        assert store.settings.runtime_values() == {}
        assert store.settings.note("unrelated") == "keep"
        assert store.db.execute("SELECT COUNT(*) FROM runtime_config_startup").fetchone()[0]
        assert runtime.reset_all() == 0
    finally:
        store.close()


def test_reset_all_keeps_cache_on_failed_delete():
    store = Store.create(":memory:")
    try:
        runtime = RuntimeSettings(_base(channel_context_chars=6000), store)
        runtime.set_text("channel_context_chars", "4300")
        store.db.execute(
            "CREATE TRIGGER reject_delete BEFORE DELETE ON runtime_config "
            "BEGIN SELECT RAISE(ABORT, 'reject'); END"
        )
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            runtime.reset_all()
        assert runtime.channel_context_chars == 4300
        assert runtime.source("channel_context_chars") == "db"
        assert "channel_context_chars" in store.settings.runtime_values()
    finally:
        store.close()
