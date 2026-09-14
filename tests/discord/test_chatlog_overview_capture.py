import unittest
from types import SimpleNamespace as NS

from hina_bot.chatlog_commands import ChatLogCommands
from hina_bot.store import Store
from hina_bot.discord.chatlog_capture import (
    capture_mode_overrides,
    set_capture_mode_override,
)
from hina_bot.discord.chatlog_capture_commands import overview_rows


class ChatLogOverviewCaptureTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        guild = NS(
            id=1,
            name="테스트 서버",
            text_channels=[NS(id=10, name="일반"), NS(id=11, name="봇")],
            threads=[],
        )
        inherited = NS(
            id=2,
            name="상속 서버",
            text_channels=[NS(id=20, name="일반")],
            threads=[],
        )
        self.group = ChatLogCommands(NS(
            store=self.store,
            guilds=[guild, inherited],
            settings=NS(allowed_guild_ids=frozenset()),
            emoji_admin_ids={100},
        ))

    def tearDown(self):
        self.store.close()

    def test_capture_override_listing(self):
        set_capture_mode_override(self.store, "global", "all")
        set_capture_mode_override(self.store, "guild:1", "direct")
        set_capture_mode_override(self.store, "guild:1:channel:10", "all")

        self.assertEqual(capture_mode_overrides(self.store), {
            "global": "all",
            "guild:1": "direct",
            "guild:1:channel:10": "all",
        })

    def test_compact_overview_includes_log_or_capture_overrides(self):
        self.store.set_chat_log_mode_override("global", "on")
        self.store.set_chat_log_mode_override("guild:1:channel:11", "off")
        set_capture_mode_override(self.store, "global", "all")
        set_capture_mode_override(self.store, "guild:1", "direct")

        compact = overview_rows(self.group, 100, "overrides")

        self.assertEqual(compact, [
            ["전역", "GLOBAL", "on", "on", "all", "all"],
            ["서버", "테스트 서버", "상속", "on", "direct", "direct"],
            ["채널", "테스트 서버/#봇", "off", "off", "상속", "direct"],
        ])

    def test_full_overview_shows_inherited_capture_effective_value(self):
        set_capture_mode_override(self.store, "global", "direct")

        rows = overview_rows(self.group, 100, "all")

        self.assertIn(
            ["서버", "상속 서버", "상속", "on", "상속", "direct"],
            rows,
        )
        self.assertIn(
            ["채널", "상속 서버/#일반", "상속", "on", "상속", "direct"],
            rows,
        )


if __name__ == "__main__":
    unittest.main()
