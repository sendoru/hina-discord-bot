"""Stage 1: Discord scope picker preserves current defaults and enforces boundaries."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.core.recent import RecentMessages
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.chatlog_commands import ChatLogCommands
from hina_bot.discord.memory_commands import MemoryCommands
from hina_bot.discord.scope_targets import command_target_scope


def _channel(channel_id: int, *, guild_id: int = 1, visible: bool = True):
    return NS(
        id=channel_id,
        guild=NS(id=guild_id),
        permissions_for=lambda _user: NS(view_channel=visible),
    )


def _interaction(*, guild_id=1, channel_id=10):
    return NS(
        guild_id=guild_id,
        channel_id=channel_id,
        user=NS(id=100),
        response=NS(send_message=AsyncMock(), defer=AsyncMock()),
        followup=NS(send=AsyncMock()),
    )


@pytest.mark.parametrize("guild_id,channel,reason", [
    (1, _channel(20, guild_id=2), "현재 서버"),
    (1, _channel(20, visible=False), "조회 권한"),
    (None, _channel(20), "DM"),
])
def test_cross_scope_picker_fails_closed(guild_id, channel, reason):
    with pytest.raises(ValueError, match=reason):
        command_target_scope(_interaction(guild_id=guild_id), channel=channel)


@pytest.mark.parametrize("target", ["server", "global"])
def test_picker_rejects_non_channel_targets(target):
    with pytest.raises(ValueError, match="target:channel"):
        command_target_scope(_interaction(), target=target, channel=_channel(20))


def test_default_scope_is_current_and_selected_scope_uses_same_user():
    interaction = _interaction()
    assert command_target_scope(interaction) == Scope(1, 10, 100)
    assert command_target_scope(interaction, channel=_channel(20)) == Scope(1, 20, 100)


def test_scope_picker_is_registered_for_channel_specific_commands():
    memory = MemoryCommands(NS(emoji_admin_ids={100}))
    # The chatlog constructor runs its legacy migration.
    store = Store(":memory:")
    try:
        chatlog = ChatLogCommands(NS(store=store, emoji_admin_ids={100}))
        for command in (memory.get_command("mode"), memory.get_command("purge"), chatlog.get_command("mode")):
            assert "channel" in command._params
            assert not command._params["channel"].required
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_mode_targets_selected_channel_and_reports_its_effective_state():
    store = Store(":memory:")
    try:
        lock = asyncio.Lock()
        client = NS(store=store, channel_lock=lambda _scope: lock)
        group = MemoryCommands(client)
        interaction = _interaction()
        await group.mode.callback(group, interaction, "off", "channel", _channel(20))
        assert store.memory_mode(Scope(1, 10, 100)) == "normal"
        assert store.memory_mode(Scope(1, 20, 100)) == "off"
        assert "<#20>" in interaction.followup.send.call_args.args[0]
        assert "최종 적용: **off**" in interaction.followup.send.call_args.args[0]
        assert interaction.followup.send.call_args.kwargs["ephemeral"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_mode_refuses_cross_guild_channel_without_writing():
    store = Store(":memory:")
    try:
        group = MemoryCommands(NS(store=store, channel_lock=lambda _: asyncio.Lock()))
        interaction = _interaction()
        await group.mode.callback(group, interaction, "off", "channel", _channel(20, guild_id=2))
        assert store.memory_mode_overrides() == {}
        assert "현재 서버" in interaction.response.send_message.call_args.args[0]
        interaction.response.defer.assert_not_awaited()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_chatlog_mode_only_invalidates_selected_channel_buffer():
    store, recent = Store(":memory:"), RecentMessages()
    try:
        current, selected, sibling = (Scope(1, n, 100) for n in (10, 20, 30))
        for i, scope in enumerate((current, selected, sibling), 1):
            recent.add(scope, i, "author", f"turn {i}")
            recent.mark_hydrated(scope)
        group = ChatLogCommands(NS(
            store=store, recent=recent, channel_lock=lambda _: asyncio.Lock(), emoji_admin_ids={100},
        ))
        interaction = _interaction()
        await group.mode.callback(group, interaction, "direct", "channel", _channel(20))
        assert store.chat_log_mode_override(selected.channel) == "on"
        assert store.note(f"config:chatlog_capture:{selected.channel}") == "direct"
        assert recent.context(selected, 999) == []
        assert recent.needs_hydration(selected)
        for scope in (current, sibling):
            assert len(recent.context(scope, 999)) == 1
            assert not recent.needs_hydration(scope)
        assert "<#20>" in interaction.followup.send.call_args.args[0]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_purge_selected_channel_requires_confirmation_and_preserves_other_channels():
    store = Store(":memory:")
    try:
        current, selected, sibling = (Scope(1, n, 100) for n in (10, 20, 30))
        for index, scope in enumerate((current, selected, sibling), 1):
            store.add(scope, index, f"turn {index}", f"reply {index}")
        group = MemoryCommands(NS(store=store, channel_lock=lambda _: asyncio.Lock()))
        interaction = _interaction()
        chosen = _channel(20)

        await group.purge.callback(group, interaction, "channel", False, chosen)
        assert len(store.history(selected)) == 1
        assert "<#20>" in interaction.response.send_message.call_args.args[0]

        await group.purge.callback(group, interaction, "channel", True, chosen)
        assert store.history(selected) == []
        assert len(store.history(current)) == 1
        assert len(store.history(sibling)) == 1
        assert "<#20>" in interaction.followup.send.call_args.args[0]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_purge_rejects_selected_channel_for_server_operation():
    store = Store(":memory:")
    try:
        group = MemoryCommands(NS(store=store, channel_lock=lambda _: asyncio.Lock()))
        interaction = _interaction()
        await group.purge.callback(group, interaction, "server", True, _channel(20))
        assert "target:channel" in interaction.response.send_message.call_args.args[0]
        interaction.response.defer.assert_not_awaited()
    finally:
        store.close()
