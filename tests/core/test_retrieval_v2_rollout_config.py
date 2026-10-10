from pathlib import Path

import pytest

from hina_bot.core.config import Settings, retrieval_v2_expected_backend_key
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store


def _base_env(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("LLM_PROVIDER", "openai")


def test_retrieval_v2_defaults_off_without_calibration(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    for name in (
        "RETRIEVAL_V2_MODE",
        "RETRIEVAL_V2_CALIBRATION_BACKEND_KEY",
        "RETRIEVAL_V2_FACTUAL_REJECT",
        "RETRIEVAL_V2_FACTUAL_STRONG",
        "RETRIEVAL_V2_AMBIENT_REJECT",
        "RETRIEVAL_V2_AMBIENT_STRONG",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.load()

    assert settings.retrieval_v2_mode == "off"
    assert settings.retrieval_v2_embedding_dimensions == 768
    assert settings.retrieval_v2_calibration_backend_key == ""
    assert settings.retrieval_v2_factual_reject is None
    assert settings.retrieval_v2_ambient_reject is None


@pytest.mark.parametrize(
    "missing",
    [
        "GEMINI_API_KEY",
        "RETRIEVAL_V2_CALIBRATION_BACKEND_KEY",
        "RETRIEVAL_V2_FACTUAL_REJECT",
        "RETRIEVAL_V2_AMBIENT_STRONG",
    ],
)
def test_active_startup_requires_live_matched_calibration(
    monkeypatch, tmp_path, missing,
):
    _base_env(monkeypatch, tmp_path)
    values = {
        "GEMINI_API_KEY": "gemini-key",
        "RETRIEVAL_V2_MODE": "active",
        "RETRIEVAL_V2_CALIBRATION_BACKEND_KEY": retrieval_v2_expected_backend_key(768),
        "RETRIEVAL_V2_FACTUAL_REJECT": "0.5",
        "RETRIEVAL_V2_FACTUAL_STRONG": "0.9",
        "RETRIEVAL_V2_AMBIENT_REJECT": "0.55",
        "RETRIEVAL_V2_AMBIENT_STRONG": "0.92",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValueError):
        Settings.load()


def test_active_startup_accepts_backend_matched_calibration(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("RETRIEVAL_V2_MODE", "active")
    monkeypatch.setenv(
        "RETRIEVAL_V2_CALIBRATION_BACKEND_KEY",
        retrieval_v2_expected_backend_key(768),
    )
    monkeypatch.setenv("RETRIEVAL_V2_FACTUAL_REJECT", "0.5")
    monkeypatch.setenv("RETRIEVAL_V2_FACTUAL_STRONG", "0.9")
    monkeypatch.setenv("RETRIEVAL_V2_AMBIENT_REJECT", "0.55")
    monkeypatch.setenv("RETRIEVAL_V2_AMBIENT_STRONG", "0.92")

    settings = Settings.load()

    assert settings.retrieval_v2_mode == "active"
    assert settings.retrieval_v2_calibration_backend_key == (
        retrieval_v2_expected_backend_key(768)
    )


def test_runtime_mode_is_immediate_rollback_switch():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(
            Settings(discord_token="token", openai_api_key="key"),
            store,
        )
        assert settings.retrieval_v2_mode == "off"
        assert settings.set_text("RETRIEVAL_V2_MODE", "shadow") == "shadow"
        assert settings.retrieval_v2_mode == "shadow"
        assert settings.set_text("RETRIEVAL_V2_MODE", "active") == "active"
        assert settings.set_text("RETRIEVAL_V2_MODE", "off") == "off"
        assert settings.retrieval_v2_mode == "off"
        with pytest.raises(ValueError):
            settings.set_text("RETRIEVAL_V2_MODE", "canary")
    finally:
        store.close()
