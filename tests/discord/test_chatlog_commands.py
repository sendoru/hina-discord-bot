import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.core.recent import RecentMessages
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.chatlog_capture import set_capture_mode_override
from hina_bot.discord.chatlog_commands import ChatLogCommands, _set_mode_override


class ChatLogCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_access_control_and_command_surface(self):
        store = Store(":memory:")
        try:
            group = ChatLogCommands(NS(store=store, emoji_admin_ids={100, 101}))
            interaction = NS(user=NS(id=200), response=NS(send_message=AsyncMock()))
            self.assertFalse(await group.interaction_check(interaction))
            interaction.user.id = 100
            self.assertTrue(await group.interaction_check(interaction))
            self.assertEqual(
                {command.name for command in group.commands},
                {"mode", "status", "overview", "clear"},
            )
            self.assertIsNone(group.get_command("capture"))
        finally:
            store.close()

    def test_available_in_guilds_and_private_contexts(self):
        store = Store(":memory:")
        try:
            group = ChatLogCommands(NS(store=store, emoji_admin_ids={100}))
            self.assertTrue(group.allowed_contexts.guild)
            self.assertTrue(group.allowed_contexts.dm_channel)
            self.assertTrue(group.allowed_contexts.private_channel)
            self.assertTrue(group.allowed_installs.guild)
            self.assertTrue(group.allowed_installs.user)
        finally:
            store.close()

    async def test_mode_direct_clears_target_buffer_and_keeps_chatlog_enabled(self):
        store, recent, lock = Store(":memory:"), RecentMessages(), asyncio.Lock()
        scope, other = Scope(1, 10, 100), Scope(1, 20, 100)
        recent.add(scope, 1, "A", "current")
        recent.add(other, 2, "A", "other channel")
        client = NS(
            store=store,
            recent=recent,
            channel_lock=lambda _: lock,
            emoji_admin_ids={100},
        )
        group = ChatLogCommands(client)
        interaction = NS(
            guild_id=1,
            channel_id=10,
            user=NS(id=100),
            response=NS(defer=AsyncMock(), send_message=AsyncMock()),
            followup=NS(send=AsyncMock()),
        )

        await group.mode.callback(group, interaction, "direct", "channel")

        self.assertTrue(store.chat_log_enabled(scope))
        self.assertEqual(store.note(f"config:chatlog_capture:{scope.channel}"), "direct")
        self.assertEqual(recent.context(scope, 3), [])
        self.assertEqual(len(recent.context(other, 3)), 1)
        text = interaction.followup.send.call_args.args[0]
        self.assertIn("최종 적용: **direct**", text)
        store.close()

    async def test_mode_off_clears_only_target_buffer_and_not_memory(self):
        store, recent, lock = Store(":memory:"), RecentMessages(), asyncio.Lock()
        scope, other = Scope(1, 10, 100), Scope(1, 20, 100)
        store.add(scope, 10, "keep", "reply")
        recent.add(scope, 1, "A", "current")
        recent.add(other, 2, "A", "other channel")
        client = NS(
            store=store,
            recent=recent,
            channel_lock=lambda _: lock,
            emoji_admin_ids={100},
        )
        group = ChatLogCommands(client)
        interaction = NS(
            guild_id=1,
            channel_id=10,
            user=NS(id=100),
            response=NS(defer=AsyncMock(), send_message=AsyncMock()),
            followup=NS(send=AsyncMock()),
        )

        await group.mode.callback(group, interaction, "off", "channel")

        self.assertFalse(store.chat_log_enabled(scope))
        self.assertEqual(store.memory_mode(scope), "normal")
        self.assertEqual(len(store.history(scope)), 1)
        self.assertEqual(recent.context(scope, 3), [])
        self.assertEqual(len(recent.context(other, 3)), 1)
        text = interaction.followup.send.call_args.args[0]
        self.assertIn("최종 적용: **off**", text)
        store.close()

    async def test_inherit_removes_both_backing_overrides_and_uses_parent_policy(self):
        store, recent, lock = Store(":memory:"), RecentMessages(), asyncio.Lock()
        scope = Scope(1, 10, 100)
        client = NS(
            store=store,
            recent=recent,
            channel_lock=lambda _: lock,
            emoji_admin_ids={100},
        )
        group = ChatLogCommands(client)
        _set_mode_override(store, "global", "direct")
        _set_mode_override(store, scope.realm, "all")
        _set_mode_override(store, scope.channel, "off")
        interaction = NS(
            guild_id=1,
            channel_id=10,
            user=NS(id=100),
            response=NS(defer=AsyncMock(), send_message=AsyncMock()),
            followup=NS(send=AsyncMock()),
        )

        await group.mode.callback(group, interaction, "inherit", "channel")

        self.assertIsNone(store.chat_log_mode_override(scope.channel))
        self.assertEqual(store.note(f"config:chatlog_capture:{scope.channel}"), "")
        self.assertTrue(store.chat_log_enabled(scope))
        text = interaction.followup.send.call_args.args[0]
        self.assertIn("최종 적용: **all**", text)
        store.close()

    async def test_global_cannot_inherit_and_server_target_requires_guild(self):
        store = Store(":memory:")
        client = NS(
            store=store,
            recent=RecentMessages(),
            channel_lock=lambda _: asyncio.Lock(),
            emoji_admin_ids={100},
        )
        group = ChatLogCommands(client)
        interaction = NS(
            guild_id=None,
            channel_id=10,
            user=NS(id=100),
            response=NS(defer=AsyncMock(), send_message=AsyncMock()),
            followup=NS(send=AsyncMock()),
        )

        await group.mode.callback(group, interaction, "inherit", "global")
        self.assertIn("전역 chatlog", interaction.response.send_message.call_args.args[0])
        interaction.response.send_message.reset_mock()
        await group.mode.callback(group, interaction, "off", "server")
        self.assertIn("DM에서는 서버 설정", interaction.response.send_message.call_args.args[0])
        store.close()

    def test_legacy_mode_and_capture_settings_are_migrated_to_one_policy(self):
        store = Store(":memory:")
        store.set_chat_log_mode_override("global", "on")
        set_capture_mode_override(store, "global", "direct")
        store.set_chat_log_mode_override("guild:1", "off")

        group = ChatLogCommands(NS(store=store, emoji_admin_ids={100}))
        global_scope = Scope(2, 20, 100)
        disabled_scope = Scope(1, 10, 100)

        self.assertIn("최종 적용: **direct**", group._status_text(global_scope))
        self.assertIn("최종 적용: **off**", group._status_text(disabled_scope))
        self.assertEqual(store.note("config:chatlog_unified_v1"), "1")
        store.close()

    def test_overview_lists_one_unified_policy_column(self):
        store = Store(":memory:")
        guild = NS(
            id=1,
            name="테스트 서버",
            text_channels=[NS(id=10, name="일반"), NS(id=11, name="봇")],
            threads=[],
        )
        inherited_guild = NS(
            id=2,
            name="상속 서버",
            text_channels=[NS(id=20, name="일반")],
            threads=[],
        )
        client = NS(
            store=store,
            guilds=[guild, inherited_guild],
            settings=NS(allowed_guild_ids=frozenset()),
            emoji_admin_ids={100},
        )
        group = ChatLogCommands(client)
        _set_mode_override(store, "global", "direct")
        _set_mode_override(store, "guild:1", "all")
        _set_mode_override(store, "guild:1:channel:11", "off")

        rows = group._overview_rows(100, "all")
        self.assertIn(["전역", "GLOBAL", "direct", "direct"], rows)
        self.assertIn(["서버", "테스트 서버", "all", "all"], rows)
        self.assertIn(["채널", "테스트 서버/#일반", "상속", "all"], rows)
        self.assertIn(["채널", "테스트 서버/#봇", "off", "off"], rows)
        self.assertIn(["서버", "상속 서버", "상속", "direct"], rows)

        compact = group._overview_rows(100, "overrides")
        self.assertEqual(
            compact,
            [
                ["전역", "GLOBAL", "direct", "direct"],
                ["서버", "테스트 서버", "all", "all"],
                ["채널", "테스트 서버/#봇", "off", "off"],
            ],
        )
        store.close()

    async def test_clear_only_drops_current_channel_recent_buffer(self):
        store, recent = Store(":memory:"), RecentMessages()
        scope, other = Scope(1, 10, 100), Scope(1, 20, 100)
        store.add(scope, 10, "keep", "reply")
        recent.add(scope, 1, "A", "current")
        recent.add(other, 2, "B", "other")
        client = NS(
            store=store,
            recent=recent,
            channel_lock=lambda _: asyncio.Lock(),
            emoji_admin_ids={100},
        )
        group = ChatLogCommands(client)
        interaction = NS(
            guild_id=1,
            channel_id=10,
            user=NS(id=100),
            response=NS(send_message=AsyncMock()),
        )

        await group.clear.callback(group, interaction)

        self.assertEqual(recent.context(scope, 3), [])
        self.assertEqual(len(recent.context(other, 3)), 1)
        self.assertEqual(len(store.history(scope)), 1)
        self.assertIn("장기 기억은 그대로", interaction.response.send_message.call_args.args[0])
        store.close()
