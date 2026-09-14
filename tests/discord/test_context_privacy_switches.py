from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from hina_bot.config import Settings
from hina_bot.routing import Scope
from hina_bot.store import Store
from hina_bot.web_bot import HinaClient

from hina_bot.discord.config_commands import ConfigCommands


@pytest.fixture
def bot():
    store = Store(":memory:")
    client = HinaClient(
        Settings("test", "test", cooldown=0, bot_admin_ids=frozenset({100})),
        store=store,
        llm=NS(close=AsyncMock()),
    )
    yield client
    store.close()


@pytest.mark.asyncio
async def test_global_chatlog_mode_change_clears_all_recent_rows_and_hydration_state(bot):
    first = Scope(1, 10, 100)
    second = Scope(2, 20, 200)
    bot.recent.add(first, 1, "A", "ambient one")
    bot.recent.add(second, 2, "B", "ambient two")
    bot.recent.mark_hydrated(first)
    bot.recent.mark_hydrated(second)

    group = bot.tree.get_command("chatlog")
    mode = group.get_command("mode")
    interaction = NS(
        guild_id=1,
        channel_id=10,
        user=NS(id=100),
        response=NS(defer=AsyncMock()),
        followup=NS(send=AsyncMock()),
    )
    await mode.callback(group, interaction, "direct", "global")

    assert bot.recent.buffers == {}
    assert bot.recent.hydrated == set()
    assert bot.store.chat_log_enabled(first)
    assert bot.store.note("config:chatlog_capture:global") == "direct"
    assert group.get_command("capture") is None


def test_external_context_policy_hot_change_clears_all_recent_rows_and_hydration(bot):
    first = Scope(1, 10, 100)
    second = Scope(2, 20, 200)
    bot.recent.add(first, 1, "A", "ambient one")
    bot.recent.add(second, 2, "B", "ambient two")
    bot.recent.mark_hydrated(first)
    bot.recent.mark_hydrated(second)

    # Runtime entry normally wraps Settings in RuntimeSettings. The side effect itself only needs
    # the client/recent objects, so call it directly here to lock down invalidation semantics.
    ConfigCommands(bot)._apply_side_effects("external_context_policy")

    assert bot.recent.buffers == {}
    assert bot.recent.hydrated == set()
