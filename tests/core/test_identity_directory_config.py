from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.core.config import Settings
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

