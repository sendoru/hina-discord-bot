from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.bot import HinaClient


@pytest.mark.parametrize("enabled", [False, True])
def test_member_intent_and_startup_chunking_are_opt_in(enabled):
    store = Store(":memory:")
    client = HinaClient(
        Settings(discord_token="test", discord_members_intent=enabled),
        store=store, llm=NS(close=AsyncMock()),
    )
    try:
        assert client.intents.members is enabled
        assert client._connection.member_cache_flags.joined is enabled
        assert client._connection._chunk_guilds is enabled
    finally:
        store.close()


@pytest.mark.parametrize("value,enabled", [("true", True), ("false", False)])
def test_member_intent_startup_environment(monkeypatch, tmp_path, value, enabled):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("DISCORD_MEMBERS_INTENT", value)
    assert Settings.load().discord_members_intent is enabled


def test_invalid_member_intent_environment_is_rejected(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("DISCORD_MEMBERS_INTENT", "yes")
    with pytest.raises(ValueError, match="DISCORD_MEMBERS_INTENT"):
        Settings.load()


def test_historical_alias_filter_is_applied_per_source_before_aggregation():
    store = Store(":memory:")
    try:
        store.add_shared_call(Scope(1, 10, 200, True), 1, "visible-old", "공개")
        store.add_shared_call(Scope(1, 11, 200, True), 2, "now-hidden", "예전 공개")
        rows = store.identity_candidates(1, allowed_channel_ids={10})
        assert rows[0]["names"] == ["visible-old"]
        assert rows[0]["channel_ids"] == [10]
        assert store.identity_candidates(1, allowed_channel_ids=set()) == []
    finally:
        store.close()
