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

    assert settings.rag_mode == "v1"
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
        with pytest.raises(ValueError, match="active"):
            settings.set_text("RETRIEVAL_V2_MODE", "active")
        assert settings.set_text("RETRIEVAL_V2_MODE", "off") == "off"
        assert settings.retrieval_v2_mode == "off"
        with pytest.raises(ValueError):
            settings.set_text("RETRIEVAL_V2_MODE", "canary")
    finally:
        store.close()

    store = Store(":memory:")
    try:
        settings = RuntimeSettings(
            Settings(
                discord_token="token",
                openai_api_key="key",
                gemini_api_key="gemini-key",
                retrieval_v2_calibration_backend_key=(
                    retrieval_v2_expected_backend_key(768)
                ),
                retrieval_v2_factual_reject=0.5,
                retrieval_v2_factual_strong=0.9,
                retrieval_v2_ambient_reject=0.55,
                retrieval_v2_ambient_strong=0.92,
            ),
            store,
        )
        assert settings.set_text("RETRIEVAL_V2_MODE", "active") == "active"
        assert settings.retrieval_v2_mode == "active"
        assert settings.set_text("RETRIEVAL_V2_MODE", "off") == "off"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("rag", "legacy", "expected"),
    [
        ("", "off", "v1"),
        ("", "shadow", "shadow"),
        ("", "active", "v2"),
        ("off", "active", "off"),
        ("v1", "active", "v1"),
        ("shadow", "off", "shadow"),
    ],
)
def test_rag_env_precedence_and_legacy_mapping(monkeypatch, tmp_path, rag, legacy, expected):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RETRIEVAL_V2_MODE", legacy)
    if rag:
        monkeypatch.setenv("RAG_MODE", rag)
    else:
        monkeypatch.delenv("RAG_MODE", raising=False)
    if expected == "v2":
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv(
            "RETRIEVAL_V2_CALIBRATION_BACKEND_KEY",
            retrieval_v2_expected_backend_key(768),
        )
        for key, value in (
            ("RETRIEVAL_V2_FACTUAL_REJECT", "0.5"),
            ("RETRIEVAL_V2_FACTUAL_STRONG", "0.9"),
            ("RETRIEVAL_V2_AMBIENT_REJECT", "0.55"),
            ("RETRIEVAL_V2_AMBIENT_STRONG", "0.92"),
        ):
            monkeypatch.setenv(key, value)
    assert Settings.load().rag_mode == expected


def test_rag_runtime_off_v1_shadow_and_legacy_alias_persist():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(
            Settings(discord_token="token", openai_api_key="key"), store
        )
        assert settings.rag_mode == "v1"
        assert settings.set_text("RAG_MODE", "off") == "off"
        assert settings.rag_mode == "off"
        assert settings.retrieval_v2_mode == "off"
        assert settings.set_text("RAG_MODE", "shadow") == "shadow"
        assert settings.rag_mode == "shadow"
        assert settings.set_text("RETRIEVAL_V2_MODE", "off") == "off"
        assert settings.rag_mode == "v1"
        assert settings.source("RAG_MODE") == "db"
        settings.reload()
        assert settings.rag_mode == "v1"
        assert settings.set_text("RAG_MODE", "off") == "off"
        assert settings.reset("RAG_MODE") == "v1"
        assert settings.rag_mode == "v1"
        with pytest.raises(ValueError, match="RAG_MODE"):
            settings.set_text("RAG_MODE", "active")
        with pytest.raises(ValueError, match="RAG_MODE"):
            settings.set_text("RAG_MODE", "unexpected")
    finally:
        store.close()


@pytest.mark.parametrize(
    ("previous", "expected"),
    [("off", "v1"), ("shadow", "shadow"), ("active", "v2")],
)
def test_old_database_override_is_migrated(previous, expected):
    store = Store(":memory:")
    try:
        store.db.execute(
            """CREATE TABLE runtime_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        store.db.execute(
            "INSERT INTO runtime_config(key,value) VALUES (?,?)",
            ("retrieval_v2_mode", f'"{previous}"'),
        )
        settings = RuntimeSettings(
            Settings(discord_token="token", openai_api_key="key"), store
        )
        assert settings.rag_mode == expected
        assert settings.source("RAG_MODE") == "db"
        keys = [
            row[0]
            for row in store.db.execute("SELECT key FROM runtime_config").fetchall()
        ]
        assert keys == ["rag_mode"]
        settings.reset("RAG_MODE")
        assert settings.rag_mode == "v1"
        settings.reload()
        assert settings.rag_mode == "v1"
    finally:
        store.close()
