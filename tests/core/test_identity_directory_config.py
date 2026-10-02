from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.bot import HinaClient


def test_member_intent_and_startup_chunking_are_always_enabled():
    store = Store(":memory:")
    client = HinaClient(
        Settings(discord_token="test"),
        store=store, llm=NS(close=AsyncMock()),
    )
    try:
        assert client.intents.members is True
        assert client._connection.member_cache_flags.joined is True
        assert client._connection._chunk_guilds is True
    finally:
        store.close()


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
