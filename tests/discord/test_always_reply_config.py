"""Stage 2: context-aware runtime Discord ID mutations and Dashboard parity."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store
from hina_bot.discord.config_commands import ConfigCommands


def _channel(identifier, *, guild_id=1, visible=True):
    return NS(
        id=identifier,
        guild=NS(id=guild_id),
        permissions_for=lambda _user: NS(view_channel=visible),
    )


def _interaction(*, guild_id=1, channel_id=10, user_id=100):
    return NS(
        guild_id=guild_id,
        channel_id=channel_id,
        user=NS(id=user_id),
        response=NS(send_message=AsyncMock()),
    )


@pytest.fixture
def config_context():
    store = Store(":memory:")
    startup = Settings(
        discord_token="test", openai_api_key="test",
        always_reply_channel_ids=frozenset({10, 20}),
    )
    settings = RuntimeSettings(startup, store)
    recent = NS(clear_all=Mock())
    client = NS(settings=settings, recent=recent, emoji_admin_ids={100})
    yield store, client, ConfigCommands(client)
    store.close()


@pytest.mark.asyncio
async def test_default_and_explicit_channel_toggle_without_replacing_other_ids(config_context):
    _, client, group = config_context
    commands = group.get_command("always-reply")
    interaction = _interaction()

    await commands.get_command("status").callback(commands, interaction)
    assert "켜짐" in interaction.response.send_message.call_args.args[0]
    assert "startup" in interaction.response.send_message.call_args.args[0]
    client.recent.clear_all.assert_not_called()

    # Removing a startup entry creates an override preserving other startup entries.
    await commands.get_command("disable").callback(commands, interaction)
    assert client.settings.always_reply_channel_ids == frozenset({20})
    assert client.settings.source("always_reply_channel_ids") == "db"
    assert client.recent.clear_all.call_count == 1

    await commands.get_command("enable").callback(commands, interaction, _channel(30))
    assert client.settings.always_reply_channel_ids == frozenset({20, 30})
    assert client.recent.clear_all.call_count == 2

    await commands.get_command("status").callback(commands, interaction, _channel(30))
    assert "켜짐" in interaction.response.send_message.call_args.args[0]
    assert "<#30>" in interaction.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_noop_keeps_startup_precedence_and_does_not_clear_recent(config_context):
    _, client, group = config_context
    commands = group.get_command("always-reply")
    interaction = _interaction()

    await commands.get_command("enable").callback(commands, interaction)
    await commands.get_command("disable").callback(commands, interaction, _channel(30))
    assert client.settings.source("always_reply_channel_ids") == "startup"
    assert client.settings.always_reply_channel_ids == frozenset({10, 20})
    client.recent.clear_all.assert_not_called()
    assert "이미 해당 상태" in interaction.response.send_message.call_args.args[0]


@pytest.mark.parametrize("guild_id,channel,reason", [
    (1, _channel(30, guild_id=2), "현재 서버"),
    (1, _channel(30, visible=False), "조회 권한"),
    (None, None, "DM"),
])
@pytest.mark.asyncio
async def test_mutations_reject_foreign_or_inaccessible_channels(
    config_context, guild_id, channel, reason,
):
    _, client, group = config_context
    command_group = group.get_command("always-reply")
    interaction = _interaction(guild_id=guild_id)
    await command_group.get_command("enable").callback(command_group, interaction, channel)
    assert reason in interaction.response.send_message.call_args.args[0]
    assert client.settings.source("always_reply_channel_ids") == "startup"
    client.recent.clear_all.assert_not_called()


@pytest.mark.asyncio
async def test_admin_group_permission_guard_and_ephemeral_responses(config_context):
    _, client, group = config_context
    denied = _interaction(user_id=200)
    assert await group.interaction_check(denied) is False
    assert denied.response.send_message.call_args.kwargs["ephemeral"]
    allowed = _interaction()
    assert await group.interaction_check(allowed) is True
    command_group = group.get_command("always-reply")
    await command_group.get_command("status").callback(command_group, allowed)
    assert allowed.response.send_message.call_args.kwargs["ephemeral"]


def test_runtime_id_mutation_uses_shared_setter_and_preserves_dashboard_changes(config_context):
    store, client, _ = config_context
    settings = client.settings
    assert settings.source("always_reply_channel_ids") == "startup"
    assert settings.update_discord_id("always_reply_channel_ids", 10, enabled=True) is False
    assert settings.source("always_reply_channel_ids") == "startup"

    # Dashboard's runtime.set uses the same setter in the bot process.
    settings.set_text("always_reply_channel_ids", "50,60")
    assert settings.update_discord_id("always_reply_channel_ids", 70, enabled=True) is True
    assert settings.always_reply_channel_ids == frozenset({50, 60, 70})
    assert settings.update_discord_id("always_reply_channel_ids", 50, enabled=False) is True
    assert settings.always_reply_channel_ids == frozenset({60, 70})

    settings.reload()
    assert settings.always_reply_channel_ids == frozenset({60, 70})
    row = store.db.execute(
        "SELECT value FROM runtime_config WHERE key='always_reply_channel_ids'"
    ).fetchone()
    assert row is not None

    settings.reset("always_reply_channel_ids")
    assert settings.always_reply_channel_ids == frozenset({10, 20})
    assert settings.source("always_reply_channel_ids") == "startup"


def test_runtime_id_mutation_validation_and_maximum_are_atomic(config_context):
    _, client, _ = config_context
    settings = client.settings

    for ident in (0, -1, True, "25"):
        with pytest.raises(ValueError):
            settings.update_discord_id("always_reply_channel_ids", ident, enabled=True)
    with pytest.raises(ValueError, match="목록 설정"):
        settings.update_discord_id("dm_always_reply", 25, enabled=True)

    settings.set_text("always_reply_channel_ids", ",".join(str(i) for i in range(1, 101)))
    with pytest.raises(ValueError, match="100"):
        settings.update_discord_id("always_reply_channel_ids", 101, enabled=True)
    assert len(settings.always_reply_channel_ids) == 100
    assert settings.source("always_reply_channel_ids") == "db"
    settings.reload()
    assert len(settings.always_reply_channel_ids) == 100


@pytest.mark.asyncio
async def test_empty_list_override_and_reset(config_context):
    _, client, group = config_context
    commands = group.get_command("always-reply")
    interaction = _interaction()
    await commands.get_command("disable").callback(commands, interaction, _channel(20))
    await commands.get_command("disable").callback(commands, interaction)
    assert client.settings.always_reply_channel_ids == frozenset()
    assert client.settings.source("always_reply_channel_ids") == "db"
    client.settings.reload()
    assert client.settings.always_reply_channel_ids == frozenset()
    assert client.settings.reset("always_reply_channel_ids") == frozenset({10, 20})
