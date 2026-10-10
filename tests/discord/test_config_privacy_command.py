from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.discord.config_commands import ConfigCommands


@pytest.mark.asyncio
async def test_privacy_command_sets_and_resets_external_context_policy():
    store = Store(":memory:")
    settings = RuntimeSettings(Settings(discord_token="test", openai_api_key="test"), store)
    recent = NS(clear_all=Mock())
    group = ConfigCommands(NS(settings=settings, recent=recent, emoji_admin_ids={100}))
    interaction = NS(response=NS(send_message=AsyncMock()))

    try:
        await group.privacy.callback(group, interaction, "full")
        assert settings.external_context_policy == "full"
        assert settings.source("external_context_policy") == "db"
        assert recent.clear_all.call_count == 1

        await group.privacy.callback(group, interaction, "startup")
        assert settings.external_context_policy == "bot_interactions_only"
        assert settings.source("external_context_policy") == "startup"
        assert recent.clear_all.call_count == 2
    finally:
        store.close()


def test_config_group_retains_context_controls_without_full_status_dump():
    group = ConfigCommands(NS())
    assert {command.name for command in group.commands} == {"privacy", "always-reply"}
    assert {command.name for command in group.get_command("always-reply").commands} == {
        "enable", "disable", "status",
    }
