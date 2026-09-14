from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from hina_bot.config import Settings
from hina_bot.store import Store

from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.discord.config_commands import ConfigCommands


@pytest.mark.asyncio
async def test_privacy_command_sets_and_resets_external_context_policy():
    store = Store(":memory:")
    settings = RuntimeSettings(Settings("test", "test"), store)
    recent = NS(clear_all=Mock())
    group = ConfigCommands(NS(settings=settings, recent=recent, emoji_admin_ids={100}))
    interaction = NS(response=NS(send_message=AsyncMock()))

    try:
        await group.privacy.callback(group, interaction, "full")
        assert settings.external_context_policy == "full"
        assert settings.source("external_context_policy") == "db"
        assert recent.clear_all.call_count == 1

        await group.privacy.callback(group, interaction, "startup")
        assert settings.external_context_policy == "direct_party_only"
        assert settings.source("external_context_policy") == "startup"
        assert recent.clear_all.call_count == 2
    finally:
        store.close()


def test_generic_config_choices_hide_external_context_policy():
    group = ConfigCommands(NS(settings=NS(), recent=NS(), emoji_admin_ids={100}))
    set_command = group.get_command("set")
    reset_command = group.get_command("reset")

    set_keys = {choice.value for choice in set_command._params["key"].choices}
    reset_keys = {choice.value for choice in reset_command._params["key"].choices}
    assert "external_context_policy" not in set_keys
    assert "external_context_policy" not in reset_keys
