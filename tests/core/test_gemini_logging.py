"""Global Gemini interaction logging with explicit domain overrides."""

import pytest

from hina_bot.core.config import Settings, gemini_store_enabled
from hina_bot.core.runtime_config import (
    RUNTIME_SETTING_SPECS,
    RuntimeSettings,
    decode_runtime_value,
    encode_runtime_value,
    format_runtime_value,
    parse_runtime_value,
)
from hina_bot.core.store import Store

DOMAINS = ("answer", "memory", "classifier", "output_repair", "knowledge")


@pytest.mark.parametrize("domain", DOMAINS)
@pytest.mark.parametrize(
    ("master", "override", "expected"),
    [
        (False, None, False),
        (True, None, True),
        (False, True, True),
        (True, False, False),
        (False, False, False),
        (True, True, True),
    ],
)
def test_domain_log_setting_inherits_unless_explicit(master, override, expected, domain):
    config = Settings(
        discord_token="token",
        provider="gemini",
        gemini_store_interactions=master,
        **{f"gemini_store_{domain}_interactions": override},
    )
    assert gemini_store_enabled(config, domain) is expected


def test_other_providers_never_use_gemini_logging():
    config = Settings(
        discord_token="token", provider="openai",
        gemini_store_interactions=True,
        gemini_store_memory_interactions=True,
    )
    assert gemini_store_enabled(config, "memory") is False
    assert gemini_store_enabled(config, "classifier", provider="openai") is False
    assert gemini_store_enabled(config, "classifier", provider="gemini") is True


def test_undefined_domain_is_rejected():
    with pytest.raises(ValueError, match="Unknown Gemini logging domain"):
        gemini_store_enabled(Settings(discord_token="token"), "misc")


def test_env_absence_and_explicit_false_have_different_semantics(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-test")
    monkeypatch.setenv("GEMINI_STORE_INTERACTIONS", "true")
    for domain in DOMAINS:
        monkeypatch.delenv(f"GEMINI_STORE_{domain.upper()}_INTERACTIONS", raising=False)

    inherited = Settings.load()
    for domain in DOMAINS:
        assert getattr(inherited, f"gemini_store_{domain}_interactions") is None
        assert gemini_store_enabled(inherited, domain) is True

    monkeypatch.setenv("GEMINI_STORE_MEMORY_INTERACTIONS", "false")
    monkeypatch.setenv("GEMINI_STORE_CLASSIFIER_INTERACTIONS", "true")
    overridden = Settings.load()
    assert overridden.gemini_store_memory_interactions is False
    assert overridden.gemini_store_classifier_interactions is True
    assert gemini_store_enabled(overridden, "memory") is False
    assert gemini_store_enabled(overridden, "answer") is True


@pytest.mark.parametrize("domain", DOMAINS)
def test_runtime_override_off_then_inherit_follows_master(domain):
    store = Store.create(":memory:")
    config = RuntimeSettings(
        Settings(discord_token="token", provider="gemini", gemini_store_interactions=True),
        store,
    )
    key = f"GEMINI_STORE_{domain.upper()}_INTERACTIONS"
    try:
        spec = RUNTIME_SETTING_SPECS[f"gemini_store_{domain}_interactions"]
        assert parse_runtime_value(spec, "inherit") is None
        assert decode_runtime_value(spec, encode_runtime_value(None)) is None
        assert format_runtime_value(None) == "inherit"
        assert gemini_store_enabled(config, domain) is True
        assert config.set_text(key, "off") is False
        assert gemini_store_enabled(config, domain) is False
        config.set_text(key, "inherit")
        assert gemini_store_enabled(config, domain) is True
        assert config.set_text("GEMINI_STORE_INTERACTIONS", "off") is False
        assert gemini_store_enabled(config, domain) is False
        assert config.set_text(key, "on") is True
        assert gemini_store_enabled(config, domain) is True
        assert config.reset(key) is None
        assert gemini_store_enabled(config, domain) is False
    finally:
        store.close()
