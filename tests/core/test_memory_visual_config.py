"""MEMORY_VISUAL_INPUT and MEMORY_VISUAL_PENDING_MAX_MB settings (#472)."""

import pytest

from hina_bot.core.config import Settings


@pytest.fixture(autouse=True)
def minimal_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.delenv("MEMORY_VISUAL_INPUT", raising=False)
    monkeypatch.delenv("MEMORY_VISUAL_PENDING_MAX_MB", raising=False)


def test_the_feature_is_off_by_default():
    settings = Settings.load()
    assert settings.memory_visual_input is False
    assert settings.memory_visual_pending_max_mb == 256


@pytest.mark.parametrize("value, expected", [("on", True), (" ON ", True), ("off", False),
                                             ("", False)])
def test_memory_visual_input_accepts_on_and_off(monkeypatch, value, expected):
    monkeypatch.setenv("MEMORY_VISUAL_INPUT", value)
    assert Settings.load().memory_visual_input is expected


@pytest.mark.parametrize("value", ["true", "1", "yes", "auto"])
def test_memory_visual_input_rejects_other_values(monkeypatch, value):
    monkeypatch.setenv("MEMORY_VISUAL_INPUT", value)
    with pytest.raises(ValueError, match="MEMORY_VISUAL_INPUT은 off 또는 on"):
        Settings.load()


@pytest.mark.parametrize("value, ok", [("16", True), ("4096", True), ("15", False),
                                       ("4097", False), ("0", False), ("-5", False)])
def test_the_disk_limit_must_leave_room_for_a_turn(monkeypatch, value, ok):
    monkeypatch.setenv("MEMORY_VISUAL_PENDING_MAX_MB", value)
    if ok:
        assert Settings.load().memory_visual_pending_max_mb == int(value)
    else:
        with pytest.raises(ValueError, match="memory visual pending"):
            Settings.load()
