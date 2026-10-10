from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.discord.config_commands import ConfigCommands, _setting_key_choices


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


def test_generic_config_key_autocomplete_scales_past_discord_choice_limit():
    all_choices = _setting_key_choices("")
    output_choices = _setting_key_choices("output")

    assert len(all_choices) <= 25
    assert "external_context_policy" not in {choice.value for choice in all_choices}
    assert {choice.value for choice in output_choices} >= {
        "output_tokens",
        "fast_output_tokens",
        "smart_output_tokens",
        "memory_output_tokens",
        "routing_classifier_max_output_tokens",
    }

    group = ConfigCommands(NS(settings=NS(), recent=NS(), emoji_admin_ids={100}))
    assert group.get_command("set") is None
    assert group.get_command("reset") is None
    assert group.get_command("always-reply") is not None


def test_config_group_retains_context_controls_without_full_status_dump():
    group = ConfigCommands(NS())
    assert {command.name for command in group.commands} == {"privacy", "always-reply"}
    assert {command.name for command in group.get_command("always-reply").commands} == {
        "enable", "disable", "status",
    }
