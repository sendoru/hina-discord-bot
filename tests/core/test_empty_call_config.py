from hina_bot.core.config import Settings


def test_empty_call_replies_load_from_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("EMPTY_CALL_REPLY", "일반 응답")
    monkeypatch.setenv("SPECIAL_DM_EMPTY_CALL_REPLY", "특수 응답")
    monkeypatch.setenv("EMPTY_RESPONSE_REPLY", "빈 모델 응답")

    settings = Settings.load()

    assert settings.empty_call_reply == "일반 응답"
    assert settings.special_dm_empty_call_reply == "특수 응답"
    assert settings.empty_response_reply == "빈 모델 응답"


def test_special_empty_call_reply_may_be_blank(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("SPECIAL_DM_EMPTY_CALL_REPLY", "")

    settings = Settings.load()

    assert settings.special_dm_empty_call_reply == ""
